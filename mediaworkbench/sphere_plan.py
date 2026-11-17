"""Frozen full-sphere source cuts and view trajectories."""
import math
import numpy as np
from . import media_sources, stitch_plan


def create(conn, asset_id, start_ms, duration_ms, width, height, horizontal_fov, keys):
    if (not stitch_plan.integer(start_ms, 0, 60000) or start_ms % 40
            or not stitch_plan.integer(duration_ms, 40, 30000) or duration_ms % 40):
        raise ValueError('sphere cuts require 25 fps boundaries, at most thirty seconds')
    if (not stitch_plan.integer(width, 32, 640) or not stitch_plan.integer(height, 32, 360)
            or width % 2 or height % 2):
        raise ValueError('sphere output dimensions must be even, within 640x360')
    if (type(horizontal_fov) not in (int, float) or not math.isfinite(horizontal_fov)
            or not 20 <= horizontal_fov <= 140):
        raise ValueError('horizontal field of view must be 20 to 140 degrees')
    if not isinstance(keys, list) or not 2 <= len(keys) <= 64:
        raise ValueError('two to sixty-four orientation keys required')
    clean, previous = [], -1
    for key in keys:
        if not isinstance(key, dict) or set(key) != {'at_ms', 'quaternion'}:
            raise ValueError('orientation key requires at_ms and xyzw quaternion')
        t, q = key['at_ms'], key['quaternion']
        if not stitch_plan.integer(t, 0, duration_ms) or t <= previous:
            raise ValueError('orientation times must increase within the saved cut')
        if (not isinstance(q, list) or len(q) != 4
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in q)):
            raise ValueError('finite xyzw quaternion required')
        norm = float(np.linalg.norm(q))
        if norm < 1e-8 or not math.isfinite(norm):
            raise ValueError('orientation has no finite direction')
        q = (np.asarray(q)/norm).tolist()
        # Exactly antipodal *rotations*, unlike opposite quaternion signs,
        # have no unique shortest turn. Require an intermediate operator key.
        if clean and abs(float(np.dot(q, clean[-1]['quaternion']))) < 1e-8:
            raise ValueError('180 degree turn needs an intermediate orientation')
        clean.append(dict(at_ms=t, quaternion=q))
        previous = t
    if clean[0]['at_ms'] != 0 or clean[-1]['at_ms'] != duration_ms:
        raise ValueError('orientation keys must cover both cut endpoints')
    source = stitch_plan.source(conn, asset_id)
    if source['width'] != 2*source['height'] or source['width'] % 2:
        raise ValueError('sphere source must be a full 2:1 equirectangular image')
    import json
    if start_ms+duration_ms > round(float(json.loads(source['metadata'])['format']['duration'])*1000):
        raise ValueError('sphere cut exceeds original recording')
    media_sources.select(conn, source)
    return stitch_plan.save(conn, 'sphere_plans', dict(version=1, source=source,
        start_ms=start_ms, frames=duration_ms//40, width=width, height=height,
        horizontal_fov=horizontal_fov, keys=clean))


def get(conn, identity):
    return stitch_plan.load(conn, 'sphere_plans', identity)
