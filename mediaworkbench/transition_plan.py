"""Freeze overlapping picture occurrences on a single exact audio clock."""
import copy
import hashlib
import json
from . import db, media_sources, planner, timeline

RATE = 48000
FPS = 25
FRAME_SAMPLES = RATE // FPS
MAX_SECONDS = 30


def schedule(snapshot, overlaps_ms):
    clips = snapshot['clips']
    if not 1 <= len(clips) <= 8:
        raise ValueError('transition timeline requires between one and eight occurrences')
    if not isinstance(overlaps_ms, list) or len(overlaps_ms) != len(clips) - 1:
        raise ValueError('one overlap is required for each neighboring pair')
    durations = [clip['end_ms'] - clip['start_ms'] for clip in clips]
    if any(type(value) is not int or value < 40 for value in durations):
        raise ValueError('transition occurrences must be at least 40 ms long')
    for index, overlap in enumerate(overlaps_ms):
        if type(overlap) is not int or not 0 <= overlap < min(durations[index:index + 2]):
            raise ValueError('overlap must be shorter than each neighboring occurrence')
    cursor = 0
    occurrences = []
    for index, clip in enumerate(clips):
        incoming = overlaps_ms[index - 1] if index else 0
        outgoing = overlaps_ms[index] if index < len(overlaps_ms) else 0
        length = durations[index] * 48
        occurrence = copy.deepcopy(clip)
        occurrence.update(at_sample=cursor, stop_sample=cursor + length,
                          fade_in=incoming * 48, fade_out=outgoing * 48,
                          occurrence=index)
        occurrences.append(occurrence)
        cursor += length - outgoing * 48
    if not 0 < cursor <= RATE * MAX_SECONDS:
        raise ValueError('transition programme must be at most 30 seconds long')
    frame_count = (cursor + FRAME_SAMPLES - 1) // FRAME_SAMPLES
    return {'version': 1, 'project': copy.deepcopy(snapshot['project']),
            'clips': occurrences, 'overlaps_ms': list(overlaps_ms),
            'samples': cursor, 'frames': frame_count,
            'output_samples': frame_count * FRAME_SAMPLES,
            'rate': RATE, 'fps': FPS, 'width': 640, 'height': 360,
            'blend': 'normalized-product-linear-light'}


def create(conn, project_id, overlaps_ms):
    source = media_sources.bind(conn, timeline.snapshot(conn, project_id))
    payload = schedule(planner.prepare(source), overlaps_ms)
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)
    checksum = hashlib.sha256(encoded.encode()).hexdigest()
    with db.write(conn):
        cursor = conn.execute('INSERT INTO transition_plans '
            '(project_id,revision,payload,payload_sha256) VALUES (?,?,?,?)',
            (project_id, payload['project']['revision'], encoded, checksum))
    return get(conn, cursor.lastrowid)


def get(conn, plan_id):
    row = conn.execute('SELECT * FROM transition_plans WHERE id=?', (plan_id,)).fetchone()
    if row is None:
        raise ValueError('transition plan not found')
    if hashlib.sha256(row['payload'].encode()).hexdigest() != row['payload_sha256']:
        raise ValueError('transition plan checksum does not match')
    payload = json.loads(row['payload'])
    if payload.get('version') != 1:
        raise ValueError('unsupported transition plan version')
    payload.update(id=plan_id, payload_sha256=row['payload_sha256'])
    return payload
