"""Frozen reference identities and isolated calibration for echo subtraction."""
import hashlib
import json
import tempfile
from pathlib import Path
from . import db, media_sources, timeline, echo_signal
from .resample_render import native_pcm


def original(conn, identity, channels):
    asset = media_sources.get(conn, identity)
    metadata = json.loads(asset['metadata'])
    audio = [s for s in metadata['streams'] if s.get('codec_type') == 'audio']
    if (not audio or int(audio[0]['sample_rate']) != 48000
            or audio[0]['channels'] != channels or float(metadata['format']['duration']) > 60):
        raise ValueError('echo sources require 48 kHz mono reference or stereo microphone, at most sixty seconds')
    return dict(asset_id=asset['id'], fingerprint=asset['fingerprint'], metadata=asset['metadata'])


def save(conn, table, payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)
    with db.write(conn):
        row = conn.execute(f'INSERT INTO {table}(payload,payload_sha256) VALUES (?,?)',
                           (encoded, hashlib.sha256(encoded.encode()).hexdigest()))
    return load(conn, table, row.lastrowid)


def load(conn, table, identity):
    row = conn.execute(f'SELECT * FROM {table} WHERE id=?', (identity,)).fetchone()
    if row is None or hashlib.sha256(row['payload'].encode()).hexdigest() != row['payload_sha256']:
        raise ValueError('echo record missing or damaged')
    payload = json.loads(row['payload'])
    if payload['version'] != 1:
        raise ValueError('unsupported echo record')
    return dict(payload, id=identity)


def profile(conn, microphone_id, reference_id, start_ms, end_ms, offset_samples=0):
    if (any(type(v) is not int for v in (start_ms, end_ms, offset_samples))
            or start_ms < 0 or not 500 <= end_ms-start_ms <= 2000 or abs(offset_samples)>48000):
        raise ValueError('echo calibration needs 500 to 2000 ms and an offset within one second')
    mic = media_sources.select(conn, original(conn, microphone_id, 2))
    ref = media_sources.select(conn, original(conn, reference_id, 1))
    first, stop = start_ms*48, end_ms*48
    with tempfile.TemporaryDirectory(prefix='echo-calibration-') as directory:
        root = Path(directory)
        with native_pcm(mic, 48000, root) as mp, native_pcm(ref, 48000, root) as rp:
            mt, rt = mp.stat().st_size//8, rp.stat().st_size//8
            if stop > mt or first+offset_samples < echo_signal.TAPS-1 or stop+offset_samples > rt:
                raise ValueError('calibration exceeds original reference or microphone')
            with mp.open('rb') as mh, rp.open('rb') as rh:
                kernel = echo_signal.identify(rh, mh, first, stop, offset_samples, rt, mt)
        media_sources.select(conn, mic)
        media_sources.select(conn, ref)
    return save(conn, 'echo_profiles', dict(version=1, microphone=mic, reference=ref,
                start_ms=start_ms, end_ms=end_ms, offset_samples=offset_samples, kernel=kernel))


def create(conn, project_id, profile_ids):
    snapshot = timeline.snapshot(conn, project_id)
    clips = snapshot['clips']
    if (not isinstance(profile_ids, list) or not 1 <= len(clips) <= 8
            or len(profile_ids) != len(clips) or any(type(v) is not int for v in profile_ids)):
        raise ValueError('one echo profile per occurrence, at most eight occurrences')
    occurrences, clock = [], 0
    for clip, identity in zip(clips, profile_ids):
        p = load(conn, 'echo_profiles', identity)
        if clip['asset_id'] != p['microphone']['asset_id'] or clip['fingerprint'] != p['microphone']['fingerprint']:
            raise ValueError('echo profile belongs to another microphone recording')
        length = (clip['end_ms']-clip['start_ms'])*48
        occurrences.append(dict(clip=clip, profile=p, offset=clock, samples=length))
        clock += length
    if clock > 1440000:
        raise ValueError('echo programme exceeds thirty seconds')
    return save(conn, 'echo_plans', dict(version=1, snapshot=snapshot, occurrences=occurrences, samples=clock))


def get(conn, identity):
    return load(conn, 'echo_plans', identity)
