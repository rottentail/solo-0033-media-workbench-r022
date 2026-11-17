"""Frozen single-camera lens calibrations, separate from planar registration."""
import math
from . import stitch_plan, lens_geometry, media_sources


def profile(conn, asset_id, focal_px, centre, matches, threshold_px=.25):
    if (type(threshold_px) not in (int, float) or not math.isfinite(threshold_px)
            or not .01 <= threshold_px <= 2):
        raise ValueError('invalid lens calibration tolerance')
    source = stitch_plan.source(conn, asset_id)
    if source['width'] % 2 or source['height'] % 2:
        raise ValueError('lens delivery requires even sensor dimensions')
    result = lens_geometry.calibrate(matches, source['width'], source['height'],
                                     focal_px, centre, threshold_px)
    media_sources.select(conn, source)
    return stitch_plan.save(conn, 'lens_profiles', dict(version=1, source=source,
        width=source['width'], height=source['height'], focal_px=focal_px,
        centre=list(centre), matches=matches, threshold_px=threshold_px, **result))


def create(conn, profile_id, start_ms, duration_ms, zoom):
    if (not stitch_plan.integer(start_ms, 0, 60000) or start_ms % 40
            or not stitch_plan.integer(duration_ms, 40, 30000) or duration_ms % 40):
        raise ValueError('lens cut requires 25 fps boundaries, at most thirty seconds')
    if type(zoom) not in (int, float) or not math.isfinite(zoom) or not 1 <= zoom <= 2:
        raise ValueError('lens crop zoom must be one to two')
    import json
    p = stitch_plan.load(conn, 'lens_profiles', profile_id)
    duration = round(float(json.loads(p['source']['metadata'])['format']['duration'])*1000)
    if start_ms+duration_ms > duration:
        raise ValueError('lens cut exceeds recording')
    lens_geometry.mapping(p, zoom)
    return stitch_plan.save(conn, 'lens_plans', dict(version=1, profile=p,
        source=p['source'], start_ms=start_ms, frames=duration_ms//40, zoom=zoom))


def get(conn, identity):
    return stitch_plan.load(conn, 'lens_plans', identity)
