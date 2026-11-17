"""Choose a complete calibrated camera sequence with a global cut budget."""
import hashlib
import json
import math
from . import db, media_sources, timeline, sync_clock


def choose(cameras, frames, minimum, cut_limit, cut_cost):
    # A state retains the current shot age: paths ending at the same camera
    # cannot be merged before their eligibility for the next cut is known.
    states = {}
    for i, camera in enumerate(cameras):
        if sync_clock.covers(camera, 0, sync_clock.FRAME):
            states[(i, 1, 0)] = (sync_clock.score(camera, 960), (i,))
    for frame in range(1, frames):
        following = {}
        for (previous, age, cuts), (value, path) in states.items():
            for i, camera in enumerate(cameras):
                if not sync_clock.covers(camera, frame*1920, (frame+1)*1920):
                    continue
                switched = i != previous
                if switched and (age < minimum or cuts >= cut_limit):
                    continue
                key = (i, 1 if switched else min(minimum, age+1), cuts+switched)
                candidate = (value + sync_clock.score(camera, frame*1920+960)
                             - (cut_cost if switched else 0), path+(i,))
                old = following.get(key)
                if old is None or candidate[0] > old[0] or (
                        candidate[0] == old[0] and candidate[1] < old[1]):
                    following[key] = candidate
        states = following
    candidates = [v for (camera, age, cuts), v in states.items() if age >= minimum]
    if not candidates:
        raise ValueError('no complete camera sequence satisfies shot and coverage limits')
    value, path = min(candidates, key=lambda item: (-item[0], item[1]))
    shots = []
    for frame, camera in enumerate(path):
        if shots and shots[-1]['camera'] == camera:
            shots[-1]['end_frame'] = frame+1
        else:
            shots.append({'camera': camera, 'start_frame': frame, 'end_frame': frame+1})
    return shots, value


def create(conn, project_id, cameras, master_asset_id, duration_ms,
           minimum_shot_ms=400, max_cuts=8, cut_cost=0):
    if type(duration_ms) is not int or not 40 <= duration_ms <= 30000 or duration_ms % 40:
        raise ValueError('multicam duration must be a whole frame, at most thirty seconds')
    if type(minimum_shot_ms) is not int or not 40 <= minimum_shot_ms <= duration_ms:
        raise ValueError('invalid minimum shot duration')
    if type(max_cuts) is not int or not 0 <= max_cuts <= 16:
        raise ValueError('invalid cut budget')
    if type(cut_cost) is not int or not 0 <= cut_cost <= 100000:
        raise ValueError('invalid cut cost')
    if not isinstance(cameras, list) or not 1 <= len(cameras) <= 4:
        raise ValueError('one to four independent camera occurrences are required')
    snapshot = timeline.snapshot(conn, project_id)
    clips = []
    for camera in cameras:
        asset = media_sources.get(conn, camera['asset_id'])
        metadata = json.loads(asset['metadata'])
        duration = float(metadata['format']['duration']) * 1000
        if not any(s['codec_type'] == 'video' for s in metadata['streams']):
            raise ValueError('camera must contain video')
        clip = {k: asset[k] for k in ('path','fingerprint','metadata')}
        clip.update(asset_id=asset['id'], start_ms=0, end_ms=round(duration), gain=1,
                    has_audio=any(s['codec_type'] == 'audio' for s in metadata['streams']),
                    clock=sync_clock.calibrate(camera['anchors'], duration), quality=[])
        previous = 0
        for interval in camera.get('quality', []):
            a, b = interval['start_ms'], interval['end_ms']
            value = interval['score']
            if (type(a) is not int or type(b) is not int or a < previous
                    or b <= a or b > duration_ms or type(value) is not int or not -1000 <= value <= 1000):
                raise ValueError('quality intervals must be ordered and disjoint')
            clip['quality'].append({'start_sample': a*48, 'end_sample': b*48, 'score':value})
            previous = b
        clips.append(clip)
    master = media_sources.get(conn, master_asset_id)
    metadata = json.loads(master['metadata'])
    if not any(s['codec_type'] == 'audio' for s in metadata['streams']) or float(metadata['format']['duration'])*1000 < duration_ms:
        raise ValueError('master recording must cover the whole programme')
    master = {k: master[k] for k in ('id','path','fingerprint','metadata')}
    master.update(asset_id=master.pop('id'), start_ms=0, end_ms=duration_ms, gain=1, has_audio=True)
    shots, value = choose(clips, duration_ms//40, math.ceil(minimum_shot_ms/40), max_cuts, cut_cost)
    plan = {'project':snapshot['project'], 'clips':clips, 'master':master, 'shots':shots,
            'frames':duration_ms//40, 'samples':duration_ms*48, 'width':640, 'height':360,
            'minimum_shot_ms':minimum_shot_ms, 'max_cuts':max_cuts, 'cut_cost':cut_cost, 'score':value}
    payload = json.dumps(plan, sort_keys=True, separators=(',',':'))
    with db.write(conn):
        cursor = conn.execute('INSERT INTO multicam_plans(project_id,payload,payload_sha256) VALUES (?,?,?)',
            (project_id,payload,hashlib.sha256(payload.encode()).hexdigest()))
    return dict(plan, id=cursor.lastrowid)


def get(conn, plan_id):
    row = conn.execute('SELECT * FROM multicam_plans WHERE id=?',(plan_id,)).fetchone()
    if row is None or hashlib.sha256(row['payload'].encode()).hexdigest() != row['payload_sha256']:
        raise ValueError('multicam plan is missing or damaged')
    return dict(json.loads(row['payload']), id=plan_id)
