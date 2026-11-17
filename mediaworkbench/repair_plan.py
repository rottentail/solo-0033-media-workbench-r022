"""Version marked source damage separately from immutable editorial repairs."""
import hashlib
import json
from . import db,timeline,media_sources
from .repair_signal import CONTEXT


def marks(conn,asset_id,intervals,expected_revision):
    asset=media_sources.get(conn,asset_id)
    metadata=json.loads(asset['metadata'])
    streams=[s for s in metadata['streams'] if s.get('codec_type')=='audio']
    if not streams or int(streams[0]['sample_rate'])!=48000 or streams[0]['channels']!=2:
        raise ValueError('repair requires stereo 48 kHz original audio')
    duration=float(metadata['format']['duration'])
    if duration>60: raise ValueError('repair source exceeds one minute')
    total=round(duration*48000)
    if type(expected_revision) is not int or expected_revision<0:
        raise ValueError('invalid repair revision')
    if not isinstance(intervals,list) or len(intervals)>32:
        raise ValueError('at most thirty-two damage intervals')
    saved=[]
    for item in intervals:
        if not isinstance(item,list) or len(item)!=2 or any(type(v) is not int for v in item):
            raise ValueError('damage interval needs two native sample positions')
        a,b=item
        if a<CONTEXT or b>total-CONTEXT or not 1<=b-a<=384:
            raise ValueError('damage needs intact context and must span one to 384 samples')
        saved.append([a,b])
    saved.sort()
    if any(b+CONTEXT>a for (_,b),(a,_) in zip(saved,saved[1:])):
        raise ValueError('damage intervals need independent intact contexts')
    with db.write(conn):
        row=conn.execute('SELECT revision FROM repair_marks WHERE asset_id=?',(asset_id,)).fetchone()
        revision=row['revision'] if row else 0
        if revision!=expected_revision:raise ValueError('repair mark revision conflict')
        conn.execute('INSERT INTO repair_marks(asset_id,revision,intervals) VALUES (?,?,?) '
                     'ON CONFLICT(asset_id) DO UPDATE SET revision=excluded.revision,intervals=excluded.intervals',
                     (asset_id,revision+1,json.dumps(saved)))
    return dict(asset_id=asset_id,revision=revision+1,intervals=saved)


def create(conn,project_id):
    conn.execute('SAVEPOINT repair_snapshot')
    try:
        snapshot=timeline.snapshot(conn,project_id)
        if not 1<=len(snapshot['clips'])<=8:raise ValueError('repair needs one to eight occurrences')
        occurrences=[];clock=0
        for clip in snapshot['clips']:
            streams=[s for s in json.loads(clip['metadata'])['streams'] if s.get('codec_type')=='audio']
            if not streams or int(streams[0]['sample_rate'])!=48000 or streams[0]['channels']!=2:
                raise ValueError('repair requires stereo 48 kHz original audio')
            if float(json.loads(clip['metadata'])['format']['duration'])>60:
                raise ValueError('repair source exceeds one minute')
            row=conn.execute('SELECT * FROM repair_marks WHERE asset_id=?',(clip['asset_id'],)).fetchone()
            saved=dict(revision=row['revision'],intervals=json.loads(row['intervals'])) if row else dict(revision=0,intervals=[])
            length=(clip['end_ms']-clip['start_ms'])*48
            occurrences.append(dict(clip=clip,marks=saved,offset=clock,samples=length));clock+=length
        if clock>1440000:raise ValueError('repair programme exceeds thirty seconds')
    finally:conn.execute('RELEASE SAVEPOINT repair_snapshot')
    payload=dict(version=1,snapshot=snapshot,occurrences=occurrences,samples=clock,rate=48000)
    encoded=json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False)
    with db.write(conn):
        row=conn.execute('INSERT INTO repair_plans(payload,payload_sha256) VALUES (?,?)',(encoded,hashlib.sha256(encoded.encode()).hexdigest()))
    return get(conn,row.lastrowid)


def get(conn,identity):
    row=conn.execute('SELECT * FROM repair_plans WHERE id=?',(identity,)).fetchone()
    if row is None or hashlib.sha256(row['payload'].encode()).hexdigest()!=row['payload_sha256']:
        raise ValueError('repair plan missing or damaged')
    payload=json.loads(row['payload'])
    if payload['version']!=1:raise ValueError('unsupported repair plan')
    return dict(payload,id=identity)
