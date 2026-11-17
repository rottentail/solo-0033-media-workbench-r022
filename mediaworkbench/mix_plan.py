"""Freeze waveform-derived ducking decisions independently of later edits."""
import copy
import hashlib
import json
from . import audio_layers, audio_signal, ducking, db


def _audio(occurrence):
    result = copy.deepcopy(occurrence)
    streams = json.loads(result['metadata'])['streams']
    result['has_audio'] = any(s.get('codec_type') == 'audio' for s in streams)
    return result


def create(conn, project_id, threshold_db=-38, attack_ms=40, release_ms=160):
    if (isinstance(threshold_db, bool) or not isinstance(threshold_db, (int, float))
            or not -80 <= threshold_db <= 0):
        raise ValueError('activity threshold must be between -80 and 0 dBFS')
    for value in (attack_ms, release_ms):
        if type(value) is not int or not 1 <= value <= 2000:
            raise ValueError('envelope ramps must be between 1 and 2000 ms')
    frozen = audio_layers.snapshot(conn, project_id)
    extent = sum(audio_signal.samples(c['end_ms'] - c['start_ms'])
                 for c in frozen['clips'])
    if not 0 < extent <= audio_signal.RATE * audio_signal.MAX_SECONDS:
        raise ValueError('audio background timeline must be between 1 ms and 600 seconds')
    frames = 0
    beds = []
    for occurrence in frozen['clips']:
        clip = _audio(occurrence)
        clip['at_sample'] = frames
        frames += audio_signal.samples(clip['end_ms'] - clip['start_ms'])
        with audio_signal.materialized(clip):
            pass
        beds.append(clip)
    if frames <= 0:
        raise ValueError('audio background timeline is empty')
    voices = []
    for occurrence in frozen['voices']:
        clip = _audio(occurrence)
        clip['at_sample'] = audio_signal.samples(clip['at_ms'])
        length = audio_signal.samples(clip['end_ms'] - clip['start_ms'])
        if clip['at_sample'] + length > frames:
            raise ValueError('voice extends beyond the background timeline')
        clip['activity'] = audio_signal.measure(clip, threshold_db)
        clip['duck_gain'] = 10 ** (clip['duck_db'] / 20)
        voices.append(clip)
    points = ducking.envelope(voices, frames, audio_signal.samples(attack_ms),
                              audio_signal.samples(release_ms))
    payload = {'version': 1, 'project': frozen['project'], 'rate': audio_signal.RATE,
               'channels': audio_signal.CHANNELS, 'frames': frames, 'beds': beds,
               'voices': voices, 'envelope': points,
               'settings': {'threshold_db': threshold_db, 'attack_ms': attack_ms,
                            'release_ms': release_ms}}
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)
    checksum = hashlib.sha256(encoded.encode()).hexdigest()
    with db.write(conn):
        cursor = conn.execute('INSERT INTO audio_mix_plans '
            '(project_id,revision,payload,payload_sha256) VALUES (?,?,?,?)',
            (project_id, frozen['project']['revision'], encoded, checksum))
    return get(conn, cursor.lastrowid)


def get(conn, plan_id):
    row = conn.execute('SELECT * FROM audio_mix_plans WHERE id=?', (plan_id,)).fetchone()
    if row is None:
        raise ValueError('audio mix plan not found')
    payload = row['payload']
    if hashlib.sha256(payload.encode()).hexdigest() != row['payload_sha256']:
        raise ValueError('stored audio mix plan checksum does not match')
    result = json.loads(payload)
    if result.get('version') != 1:
        raise ValueError('unsupported audio mix plan version')
    result.update(id=plan_id, payload_sha256=row['payload_sha256'])
    return result
