"""Freeze the selected mix and delivery loudness before later editorial changes."""
import hashlib
import json
import math
from . import db, mix_plan


def create(conn, mix_id, target_lufs=-23, ceiling_db=-1):
    for value, low, high in ((target_lufs, -30, -10), (ceiling_db, -6, -.5)):
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError('invalid mastering loudness or peak ceiling')
    mix = mix_plan.get(conn, mix_id)
    if not 19200 <= mix['frames'] <= 48000 * 30:
        raise ValueError('mastering supports programmes from 400 ms to 30 seconds')
    payload = dict(version=1, mix=mix, target_lufs=target_lufs, ceiling_db=ceiling_db)
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)
    checksum = hashlib.sha256(encoded.encode()).hexdigest()
    with db.write(conn):
        cursor = conn.execute('INSERT INTO master_plans(payload,payload_sha256) VALUES (?,?)', (encoded, checksum))
    return get(conn, cursor.lastrowid)


def get(conn, identity):
    row = conn.execute('SELECT * FROM master_plans WHERE id=?', (identity,)).fetchone()
    if row is None:
        raise ValueError('master plan not found')
    if hashlib.sha256(row['payload'].encode()).hexdigest() != row['payload_sha256']:
        raise ValueError('master plan checksum mismatch')
    plan = json.loads(row['payload'])
    if plan['version'] != 1:
        raise ValueError('unsupported master plan')
    plan['id'] = identity
    return plan
