"""Freeze source-position speed ramps and integrate their exact programme clock."""
import copy
import hashlib
import json
import math
from . import db, media_sources, planner, timeline

RATE = 48000
FPS = 25
FRAME_SAMPLES = RATE // FPS


def curve(duration_ms, knots):
    if not isinstance(knots, list) or not 2 <= len(knots) <= 16:
        raise ValueError('speed curve requires two to sixteen source-position knots')
    points = []
    for point in knots:
        if not isinstance(point, dict):
            raise ValueError('invalid speed point')
        at, speed = point['at_ms'], point['speed']
        if type(at) is not int or not 0 <= at <= duration_ms:
            raise ValueError('speed point must be inside the trimmed occurrence')
        if type(speed) not in (int, float) or not math.isfinite(speed) or not .25 <= speed <= 4:
            raise ValueError('speed must be between one quarter and four')
        if points and at <= points[-1][0]:
            raise ValueError('speed source positions must strictly increase')
        points.append((at, float(speed)))
    if points[0][0] != 0 or points[-1][0] != duration_ms:
        raise ValueError('speed curve must cover the complete trimmed occurrence')
    segments, elapsed = [], 0.0
    for (left, first), (right, last) in zip(points, points[1:]):
        extent = (right - left) / 1000
        slope = (last - first) / extent
        seconds = extent / first if abs(slope) < 1e-12 else math.log1p((last-first)/first) / slope
        segments.append({'source': left * 48, 'extent': extent * RATE,
                         'speed': first, 'slope': slope,
                         'at': elapsed * RATE, 'stop': (elapsed + seconds) * RATE})
        elapsed += seconds
    return segments, elapsed * RATE


def schedule(snapshot, curves):
    clips = snapshot['clips']
    if not 1 <= len(clips) <= 8 or not isinstance(curves, list) or len(curves) != len(clips):
        raise ValueError('one speed curve is required per occurrence, at most eight')
    occurrences, cursor = [], 0.0
    for index, (clip, specification) in enumerate(zip(clips, curves)):
        knots = specification['points'] if isinstance(specification, dict) else specification
        passes = specification.get('passes', 1) if isinstance(specification, dict) else 1
        if type(passes) is not int or not 1 <= passes <= 3:
            raise ValueError('retime passes must be between one and three')
        duration = clip['end_ms'] - clip['start_ms']
        if duration < 40:
            raise ValueError('retimed occurrences must be at least 40 ms long')
        segments, extent = curve(duration, knots)
        occurrence = copy.deepcopy(clip)
        occurrence.update(occurrence=index, at_sample=cursor, stop_sample=cursor + extent * passes,
                          speed_segments=segments, pass_samples=extent, passes=passes,
                          source_samples=duration * 48)
        occurrences.append(occurrence)
        cursor += extent * passes
    if not 0 < cursor <= RATE * 30:
        raise ValueError('retimed programme must be at most 30 seconds')
    frames = math.ceil(cursor / FRAME_SAMPLES - 1e-12)
    return {'version': 1, 'project': copy.deepcopy(snapshot['project']), 'clips': occurrences,
            'curves': copy.deepcopy(curves), 'exact_samples': cursor,
            'samples': math.ceil(cursor - 1e-9), 'frames': frames,
            'output_samples': frames * FRAME_SAMPLES, 'rate': RATE, 'fps': FPS,
            'width': 640, 'height': 360, 'audio_mode': 'speed-linked-pitch'}


def source_position(clip, programme_sample):
    local = programme_sample - clip['at_sample']
    if not 0 <= local < clip['stop_sample'] - clip['at_sample']:
        return None
    pass_samples = clip.get('pass_samples', clip['stop_sample'] - clip['at_sample'])
    pass_index = min(clip.get('passes', 1) - 1, int(local // pass_samples))
    local -= pass_index * pass_samples
    if pass_index % 2:
        local = max(0.0, pass_samples - local - 1e-7)
    segments = clip['speed_segments']
    segment = next((item for item in segments if local < item['stop']), segments[-1])
    seconds = max(0.0, local - segment['at']) / RATE
    slope = segment['slope']
    displacement = (segment['speed'] * seconds if abs(slope) < 1e-12 else
                    segment['speed'] * math.expm1(slope * seconds) / slope)
    return min(clip['source_samples'] - 1, segment['source'] + displacement * RATE)


def create(conn, project_id, curves):
    source = media_sources.bind(conn, timeline.snapshot(conn, project_id))
    payload = schedule(planner.prepare(source), curves)
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)
    checksum = hashlib.sha256(encoded.encode()).hexdigest()
    with db.write(conn):
        cursor = conn.execute('INSERT INTO retime_plans '
            '(project_id,revision,payload,payload_sha256) VALUES (?,?,?,?)',
            (project_id, payload['project']['revision'], encoded, checksum))
    return get(conn, cursor.lastrowid)


def get(conn, plan_id):
    row = conn.execute('SELECT * FROM retime_plans WHERE id=?', (plan_id,)).fetchone()
    if row is None or hashlib.sha256(row['payload'].encode()).hexdigest() != row['payload_sha256']:
        raise ValueError('retime plan absent or checksum does not match')
    result = json.loads(row['payload'])
    if result.get('version') != 1:
        raise ValueError('unsupported retime plan version')
    result.update(id=plan_id, payload_sha256=row['payload_sha256'])
    return result
