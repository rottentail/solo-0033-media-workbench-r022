"""Freeze native-rate occurrences on a single destination sample clock."""
import hashlib
import json
from . import db, timeline

RATES = (44100, 48000, 96000)


def boundary(milliseconds, rate):
    return (milliseconds * rate + 500) // 1000


def create(conn, project_id, rate=44100):
    if type(rate) is not int or rate not in RATES:
        raise ValueError('delivery rate must be 44100, 48000 or 96000')
    snapshot = timeline.snapshot(conn, project_id)
    if not 1 <= len(snapshot['clips']) <= 8:
        raise ValueError('rate delivery needs one to eight occurrences')
    occurrences = []
    clock = 0
    for clip in snapshot['clips']:
        metadata = json.loads(clip['metadata'])
        streams = [s for s in metadata['streams'] if s.get('codec_type') == 'audio']
        if not streams:
            raise ValueError('rate delivery requires original audio')
        stream = streams[0]
        native = int(stream['sample_rate'])
        if not 8000 <= native <= 192000 or stream['channels'] not in (1, 2):
            raise ValueError('native audio must be mono or stereo, 8 to 192 kHz')
        if float(metadata['format']['duration']) > 60:
            raise ValueError('original audio exceeds one minute')
        duration = clip['end_ms'] - clip['start_ms']
        end = clock + duration
        occurrences.append(dict(clip=clip, native_rate=native, offset_ms=clock,
                                first=boundary(clock, rate), stop=boundary(end, rate)))
        clock = end
    if clock > 30000:
        raise ValueError('rate programme exceeds thirty seconds')
    payload = dict(version=1, rate=rate, snapshot=snapshot, occurrences=occurrences,
                   samples=boundary(clock, rate))
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)
    with db.write(conn):
        row = conn.execute('INSERT INTO resample_plans(payload,payload_sha256) VALUES (?,?)',
                           (encoded, hashlib.sha256(encoded.encode()).hexdigest()))
    return get(conn, row.lastrowid)


def get(conn, identity):
    row = conn.execute('SELECT * FROM resample_plans WHERE id=?', (identity,)).fetchone()
    if row is None or hashlib.sha256(row['payload'].encode()).hexdigest() != row['payload_sha256']:
        raise ValueError('rate delivery plan missing or damaged')
    payload = json.loads(row['payload'])
    if payload['version'] != 1:
        raise ValueError('unsupported rate delivery plan')
    return dict(payload, id=identity)
