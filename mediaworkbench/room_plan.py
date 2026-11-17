"""Freeze measured stereo room matrices independently of catalogue mutations."""
from array import array
import base64
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
from . import db, media_sources, timeline
from .assets import fingerprint
from .tools import run

RATE = 48000


def response(conn, asset_id):
    asset = media_sources.get(conn, asset_id)
    before = fingerprint(asset['path'])
    if before != asset['fingerprint']:
        raise ValueError('room response original changed')
    streams = [s for s in json.loads(asset['metadata'])['streams'] if s.get('codec_type') == 'audio']
    if not streams or streams[0].get('channels') != 4 or int(streams[0]['sample_rate']) != RATE:
        raise ValueError('room response needs four channels at 48 kHz: LL, LR, RL, RR')
    with tempfile.TemporaryDirectory(prefix='room-response-') as directory:
        path = Path(directory)/'matrix.f32'
        run('ffmpeg', ['-v','error','-y','-i',asset['path'],'-map','0:a:0','-vn',
            '-t',str(1+1/RATE),'-c:a','pcm_f32le','-f','f32le',str(path)])
        if not 0 < path.stat().st_size <= RATE*16:
            raise ValueError('response must contain one sample to one second')
        raw = path.read_bytes()
    if not raw or len(raw)%16 or len(raw)>RATE*16:
        raise ValueError('response must contain one sample to one second')
    values = array('f'); values.frombytes(raw)
    if sys.byteorder != 'little':
        values.byteswap()
    if any(not math.isfinite(v) for v in values):
        raise ValueError('nonfinite room response')
    if fingerprint(asset['path']) != before:
        raise ValueError('room response changed while reading')
    return dict(asset_id=asset_id, fingerprint=before, frames=len(raw)//16,
                pcm=base64.b64encode(raw).decode())


def kernels(saved):
    raw = base64.b64decode(saved['pcm'], validate=True)
    if len(raw) != saved['frames']*16:
        raise ValueError('damaged room response')
    values = array('f'); values.frombytes(raw)
    if sys.byteorder != 'little':
        values.byteswap()
    # Signed acoustic transfer amplitudes share one measured calibration.
    return [[float(values[4*i+c]) for i in range(saved['frames'])] for c in range(4)]


def create(conn, project_id, stages, dry=0.0, wet=1.0):
    snapshot = timeline.snapshot(conn, project_id)
    frames = sum((c['end_ms']-c['start_ms'])*48 for c in snapshot['clips'])
    if not snapshot['clips'] or len(snapshot['clips'])>8 or not 0 < frames <= RATE*30:
        raise ValueError('room programme needs one to eight clips, at most 30 seconds')
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=2 for v in (dry,wet)):
        raise ValueError('invalid dry or wet gain')
    if not isinstance(stages,list) or not 1<=len(stages)<=3:
        raise ValueError('one to three room stages required')
    frozen = []
    for i, stage in enumerate(stages):
        at, fade = stage['at_ms'], stage.get('fade_ms',0)
        if type(at) is not int or type(fade) is not int or not 0<=at*48<frames or fade<0:
            raise ValueError('invalid room stage time')
        if i == 0 and (at != 0 or fade != 0):
            raise ValueError('first room must start immediately')
        if i and (at <= stages[i-1]['at_ms'] or at < stages[i-1]['at_ms']+stages[i-1].get('fade_ms',0)):
            raise ValueError('room fades must be ordered and not overlap')
        if (at+fade)*48 > frames:
            raise ValueError('room fade extends past programme')
        frozen.append(dict(at=at*48, fade=fade*48, response=response(conn,stage['asset_id'])))
    payload = dict(version=1,snapshot=snapshot,frames=frames,stages=frozen,dry=dry,wet=wet)
    encoded = json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False)
    with db.write(conn):
        row = conn.execute('INSERT INTO room_plans(payload,payload_sha256) VALUES (?,?)',
            (encoded,hashlib.sha256(encoded.encode()).hexdigest()))
    return get(conn,row.lastrowid)


def get(conn, identity):
    row = conn.execute('SELECT * FROM room_plans WHERE id=?',(identity,)).fetchone()
    if row is None or hashlib.sha256(row['payload'].encode()).hexdigest()!=row['payload_sha256']:
        raise ValueError('room plan is missing or damaged')
    return dict(json.loads(row['payload']),id=identity)
