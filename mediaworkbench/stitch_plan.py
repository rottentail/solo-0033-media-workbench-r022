"""Immutable two-camera planar calibrations and frame-locked delivery plans."""
import hashlib
import json
import math
import numpy as np
from . import db, media_sources, stitch_geometry


def integer(value, low, high):
    return type(value) is int and low<=value<=high


def save(conn, table, payload):
    raw=json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False)
    with db.write(conn):
        row=conn.execute(f'INSERT INTO {table}(payload,payload_sha256) VALUES (?,?)',
                         (raw,hashlib.sha256(raw.encode()).hexdigest()))
    return load(conn,table,row.lastrowid)


def load(conn, table, identity):
    row=conn.execute(f'SELECT * FROM {table} WHERE id=?',(identity,)).fetchone()
    if row is None or hashlib.sha256(row['payload'].encode()).hexdigest()!=row['payload_sha256']:
        raise ValueError('stitch record missing or damaged')
    payload=json.loads(row['payload'])
    if payload['version']!=1:
        raise ValueError('unsupported stitch record')
    return dict(payload,id=identity)


def source(conn, identity):
    asset=media_sources.get(conn,identity)
    info=json.loads(asset['metadata'])
    videos=[s for s in info['streams'] if s.get('codec_type')=='video']
    if not videos or not 32<=videos[0]['width']<=640 or not 32<=videos[0]['height']<=360:
        raise ValueError('stitch video must be between 32x32 and 640x360')
    return dict(asset_id=asset['id'],fingerprint=asset['fingerprint'],metadata=asset['metadata'],
                width=videos[0]['width'],height=videos[0]['height'])


def profile(conn, left_asset_id, right_asset_id, matches, threshold_px=1.0):
    if type(threshold_px) not in (int,float) or not math.isfinite(threshold_px) or not 0.01<=threshold_px<=3:
        raise ValueError('invalid calibration tolerance')
    left,right=source(conn,left_asset_id),source(conn,right_asset_id)
    if (left['width'],left['height'])!=(right['width'],right['height']):
        raise ValueError('paired cameras require equal native dimensions')
    values=np.asarray(matches,dtype=float)
    if values.ndim!=2 or values.shape[1]!=4:
        raise ValueError('invalid landmark pairs')
    for offset in (0,2):
        if np.any(values[:,offset:offset+2]<0) or np.any(values[:,offset]>left['width']-1) or np.any(values[:,offset+1]>left['height']-1):
            raise ValueError('landmarks must be within native pictures')
    result=stitch_geometry.calibrate(matches,threshold_px)
    # A pole through the sensor is not a physically supported planar view.
    w,h=right['width'],right['height']
    corners=np.array([[0,0,1],[w-1,0,1],[0,h-1,1],[w-1,h-1,1]])
    divisor=corners@np.asarray(result['matrix'])[2]
    if not (np.all(divisor>1e-8) or np.all(divisor<-1e-8)):
        raise ValueError('registration horizon crosses the sensor')
    media_sources.select(conn,left)
    media_sources.select(conn,right)
    return save(conn,'stitch_profiles',dict(version=1,left=left,right=right,matches=matches,
                threshold_px=threshold_px,**result))


def create(conn, profile_id, left_start_ms, right_start_ms, duration_ms, canvas):
    p=load(conn,'stitch_profiles',profile_id)
    if not all(integer(v,0,60000) and v%40==0 for v in (left_start_ms,right_start_ms)) or not integer(duration_ms,40,30000) or duration_ms%40:
        raise ValueError('stitch cuts require 25 fps frame boundaries and at most thirty seconds')
    if (not isinstance(canvas,dict) or set(canvas)!={'x','y','width','height'}
        or not all(integer(canvas[k],-1280,1280) for k in ('x','y'))
        or not integer(canvas['width'],32,960) or not integer(canvas['height'],32,540)):
        raise ValueError('invalid stitch canvas')
    for which,start in (('left',left_start_ms),('right',right_start_ms)):
        if start+duration_ms>round(float(json.loads(p[which]['metadata'])['format']['duration'])*1000):
            raise ValueError('paired cut exceeds original recording')
    stitch_geometry.mapping(p['matrix'],canvas,p['left']['width'],p['left']['height'])
    return save(conn,'stitch_plans',dict(version=1,profile=p,canvas=canvas,
                left_start_ms=left_start_ms,right_start_ms=right_start_ms,frames=duration_ms//40))


def get(conn, identity):
    return load(conn,'stitch_plans',identity)
