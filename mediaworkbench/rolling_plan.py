"""Immutable gyro-assisted pure-rotation rolling shutter correction plans."""
import json
import math
import numpy as np
from . import stitch_plan, media_sources, rolling_geometry


def create(conn, asset_id, start_ms, duration_ms, horizontal_fov, zoom,
           readout_ms, direction, gyro):
    if (not stitch_plan.integer(start_ms, 0, 60000) or start_ms % 40
            or not stitch_plan.integer(duration_ms, 40, 30000) or duration_ms % 40):
        raise ValueError('rolling cut requires 25 fps boundaries, at most thirty seconds')
    for value, low, high in ((horizontal_fov, 40, 100), (zoom, 1, 2), (readout_ms, 0, 20)):
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError('invalid rolling lens, crop or readout')
    if direction not in ('top-to-bottom', 'bottom-to-top'):
        raise ValueError('invalid sensor scan direction')
    if not isinstance(gyro, list) or not 2 <= len(gyro) <= 4096:
        raise ValueError('two to 4096 gyro samples required')
    clean, previous = [], -1
    for s in gyro:
        if not isinstance(s, dict) or set(s) != {'at_ms', 'radians_per_second'}:
            raise ValueError('gyro sample needs time and body angular velocity')
        t, v = s['at_ms'], s['radians_per_second']
        if (type(t) not in (int, float) or not math.isfinite(t)
                or not 0 <= t <= 60000 or t <= previous):
            raise ValueError('gyro times must increase on original recording clock')
        if (not isinstance(v, list) or len(v) != 3
                or any(type(a) not in (int, float) or not math.isfinite(a) for a in v)
                or np.linalg.norm(v) > 2):
            raise ValueError('gyro must be finite, at most two radians per second')
        clean.append(dict(at_ms=t, radians_per_second=list(v)))
        previous = t
    source = stitch_plan.source(conn, asset_id)
    w, h = source['width'], source['height']
    if not h <= w <= 2*h or w % 2 or h % 2:
        raise ValueError('rolling sensor requires even landscape or square dimensions')
    duration = round(float(json.loads(source['metadata'])['format']['duration'])*1000)
    if start_ms+duration_ms > duration or clean[0]['at_ms'] > start_ms or clean[-1]['at_ms'] < start_ms+duration_ms:
        raise ValueError('rolling cut exceeds source or gyro coverage')
    trajectory = rolling_geometry.Trajectory(clean)
    # Check every planned frame before saving: no partial or uncovered plan.
    for frame in range(duration_ms//40):
        rolling_geometry.mapping(trajectory, start_ms+(frame+.5)*40, w, h,
                                 horizontal_fov, zoom, readout_ms, direction)
    media_sources.select(conn, source)
    return stitch_plan.save(conn, 'rolling_plans', dict(version=1, source=source,
        start_ms=start_ms, frames=duration_ms//40, horizontal_fov=horizontal_fov,
        zoom=zoom, readout_ms=readout_ms, direction=direction, gyro=clean))


def get(conn, identity):
    return stitch_plan.load(conn, 'rolling_plans', identity)
