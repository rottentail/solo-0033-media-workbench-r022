"""Revisioned phrase catalogues and immutable target-duration music plans."""
import hashlib
import json
import math
from . import db, media_sources, music_select, timeline


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _finite(value, low, high):
    return type(value) in (int,float) and math.isfinite(value) and low <= value <= high


def catalogue(conn, project_id, sections, joins, expected_revision):
    if not isinstance(sections,list) or not 1 <= len(sections) <= 8 or not isinstance(joins,list):
        raise ValueError('music catalogue needs one to eight phrases and directed joins')
    normalized = []
    for item in sections:
        name = item['name']
        if not isinstance(name,str) or not name or len(name)>64 or any(s['name']==name for s in normalized):
            raise ValueError('phrase names must be unique nonempty strings')
        start, end = item['start_ms'], item['end_ms']
        if not _integer(start,0,60000) or not _integer(end,start+100,60000):
            raise ValueError('music phrase must be at least 100 ms inside a minute source')
        asset = media_sources.get(conn,item['asset_id'])
        metadata = json.loads(asset['metadata'])
        if (end > round(float(metadata['format']['duration'])*1000)
                or float(metadata['format']['duration'])>60
                or not any(s.get('codec_type')=='audio' for s in metadata['streams'])):
            raise ValueError('music phrase must select existing audio within a minute source')
        gain, score, uses = item.get('gain',1), item.get('score',0), item.get('max_uses',1)
        if not _finite(gain,0,2) or not _integer(score,-10000,10000) or not _integer(uses,1,3):
            raise ValueError('invalid phrase gain, desirability or use limit')
        normalized.append(dict(name=name,asset_id=asset['id'],fingerprint=asset['fingerprint'],
            metadata=asset['metadata'],start_ms=start,end_ms=end,duration_ms=end-start,
            gain=gain,score=score,max_uses=uses))
    names = {s['name'] for s in normalized}
    edges = []
    seen = set()
    for edge in joins:
        key = (edge['from'],edge['to'])
        if key[0] not in names or key[1] not in names or key in seen or not _integer(edge['cost'],0,10000):
            raise ValueError('invalid or repeated directed music join')
        seen.add(key)
        edges.append(dict(edge))
    with db.write(conn):
        timeline._revision(conn,project_id)
        row = conn.execute('SELECT revision FROM music_catalogues WHERE project_id=?',(project_id,)).fetchone()
        revision = row['revision'] if row else 0
        if not _integer(expected_revision,0,1000000000) or expected_revision != revision:
            raise ValueError('music catalogue revision conflict')
        payload = json.dumps(dict(sections=normalized,joins=edges),sort_keys=True,allow_nan=False)
        conn.execute('INSERT INTO music_catalogues VALUES (?,?,?) ON CONFLICT(project_id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload',
                     (project_id,revision+1,payload))
    return dict(project_id=project_id,revision=revision+1,sections=normalized,joins=edges)


def create(conn, project_id, target_ms, overlap_ms, starts, ends):
    if not _integer(target_ms,100,30000) or not _integer(overlap_ms,0,2000):
        raise ValueError('music duration or overlap is outside supported range')
    row = conn.execute('SELECT * FROM music_catalogues WHERE project_id=?',(project_id,)).fetchone()
    if row is None:
        raise ValueError('music catalogue not found')
    catalog = json.loads(row['payload'])
    names = {s['name'] for s in catalog['sections']}
    if any(not isinstance(v,list) or not v or any(n not in names for n in v) for v in (starts,ends)):
        raise ValueError('opening and closing phrases must belong to the catalogue')
    if any(2*overlap_ms>s['duration_ms'] for s in catalog['sections']):
        raise ValueError('overlap may not exceed half any phrase')
    selected = music_select.choose(catalog['sections'],catalog['joins'],target_ms,overlap_ms,starts,ends)
    payload = dict(version=1,project_id=project_id,catalogue_revision=row['revision'],
                   catalogue=catalog,starts=starts,ends=ends,**selected)
    encoded = json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False)
    with db.write(conn):
        cursor = conn.execute('INSERT INTO music_plans(payload,payload_sha256) VALUES (?,?)',
            (encoded,hashlib.sha256(encoded.encode()).hexdigest()))
    return get(conn,cursor.lastrowid)


def get(conn, identity):
    row = conn.execute('SELECT * FROM music_plans WHERE id=?',(identity,)).fetchone()
    if row is None or hashlib.sha256(row['payload'].encode()).hexdigest()!=row['payload_sha256']:
        raise ValueError('music plan missing or damaged')
    return dict(json.loads(row['payload']),id=identity)
