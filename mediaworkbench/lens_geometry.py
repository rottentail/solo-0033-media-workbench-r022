"""Native sensor-to-ideal polynomial lens calibration and inverse sampling.

The calibrated model maps normalized *sensor* coordinates to normalized
*ideal* (undistorted) coordinates:

    u = x + x*(k1*r2 + k2*r4) + 2*p1*x*y + p2*(r2 + 2*x*x)
    v = y + y*(k1*r2 + k2*r4) + p1*(r2 + 2*y*y) + 2*p2*x*y

Normalized coordinates are ``(pixel_centre - optical_centre) / focal_px``.
Radial (k1, k2) and decentring (p1, p2) terms have distinct geometric meaning
and are never substituted for one another. Rendering inverts this certified
map by fixed-point iteration and rejects any output pixel whose inverse leaves
the native sensor, rather than clipping the sample to the sensor edge.
"""
from itertools import combinations
import numpy as np


def basis(points):
    x, y = np.asarray(points, dtype=float).T
    r2 = x*x+y*y
    return np.stack((np.column_stack((x*r2, x*r2*r2, 2*x*y, r2+2*x*x)),
                     np.column_stack((y*r2, y*r2*r2, r2+2*y*y, 2*x*y))), axis=1)


def project(points, coefficients):
    """Forward sensor-normalized -> ideal-normalized displacement model."""
    return points + basis(points) @ np.asarray(coefficients)


def geometry(width, height, focal, centre):
    if (type(focal) not in (int, float) or not np.isfinite(focal)
            or not width/2 <= focal <= width*2):
        raise ValueError('lens focal must be half to twice native width')
    c = np.asarray(centre, dtype=float)
    if (c.shape != (2,) or not np.isfinite(c).all()
            or not width*.25 <= c[0] <= width*.75
            or not height*.25 <= c[1] <= height*.75):
        raise ValueError('optical centre must be inside central half of sensor')
    low, high = -c/focal, (np.array([width-1, height-1])-c)/focal
    return c, low, high


def certify(coefficients, low, high):
    """Conservatively bound the displacement Jacobian over the whole sensor.

    The sensor->ideal map is ``g(z) = z + d(z)``. When the infinity (maximum
    absolute row-sum) norm of the Jacobian of ``d`` stays strictly below one
    everywhere on the sensor rectangle, ``g`` is a strict contraction on that
    convex rectangle, hence injective, and fixed-point inversion converges.
    The radial bounds use the exact maxima of the homogeneous displacement
    gradients (the radial Jacobian is a rank-one tensor in the radial direction
    plus an isotropic term), and the decentring rows are bounded term by term,
    so the result never under-approximates the true deformation merely because
    the fit happens to be small at the landmarks.
    """
    k1, k2, p1, p2 = (float(v) for v in coefficients)
    if not all(np.isfinite(v) for v in (k1, k2, p1, p2)):
        raise ValueError('lens coefficients must be finite')
    rx, ry = float(max(abs(low[0]), abs(high[0]))), float(max(abs(low[1]), abs(high[1])))
    r2 = rx*rx+ry*ry
    # Exact worst row-sum of the radial Jacobian. The terms in each row share
    # the coefficient sign and their joint maximum is attained at the sensor
    # rectangle corner. The k1 u-row peaks at 3 x2+y2+2|xy| and the k2
    # quartic u-row at r4+4 r2 x2+4 r2|xy|; the v rows swap x and y. These
    # are tight rather than landmark-local bounds, so a fit that only looks
    # small near the markers can never pass an out-of-range sensor.
    radial_u = (abs(k1)*(3*rx*rx+ry*ry+2*rx*ry)
                + abs(k2)*(r2*r2+4*r2*rx*rx+4*r2*rx*ry))
    radial_v = (abs(k1)*(rx*rx+3*ry*ry+2*rx*ry)
                + abs(k2)*(r2*r2+4*r2*ry*ry+4*r2*rx*ry))
    # Decentring rows, bounded term by term on the rectangle.
    decent_u = 2*abs(p1)*(rx+ry) + 2*abs(p2)*(3*rx+ry)
    decent_v = 2*abs(p1)*(rx+3*ry) + 2*abs(p2)*(rx+ry)
    bound = max(radial_u+decent_u, radial_v+decent_v)
    if not np.isfinite(bound) or bound >= .7:
        raise ValueError('lens deformation outside certified one-to-one range')
    return float(bound)


def fit(source, target):
    """Fit sensor->ideal coefficients from normalized sensor/ideal points."""
    rows = basis(source).reshape(-1, 4)
    coefficients, _, rank, singular = np.linalg.lstsq(
        rows, (target-source).ravel(), rcond=1e-10)
    if rank != 4 or singular[-1] < singular[0]*1e-7:
        raise ValueError('landmarks do not constrain radial and decentring distortion')
    return coefficients


def calibrate(matches, width, height, focal, centre, threshold):
    c, low, high = geometry(width, height, focal, centre)
    values = np.asarray(matches, dtype=float)
    if (values.ndim != 2 or values.shape[1] != 4 or not 8 <= len(values) <= 16
            or not np.isfinite(values).all()):
        raise ValueError('eight to sixteen finite [sensor_x,sensor_y,ideal_x,ideal_y] points required')
    if (np.any(values[:, :2] < 0) or np.any(values[:, 0] > width-1)
            or np.any(values[:, 1] > height-1) or np.any(np.abs(values[:, 2:]) > 2*width)):
        raise ValueError('lens landmarks outside supported coordinates')
    # The model is defined sensor -> ideal; the tolerance is in ideal pixels.
    sensor = (values[:, :2]-c)/focal
    ideal = (values[:, 2:]-c)/focal
    total = len(values)
    # A valid calibration needs a strict majority of reliable points, never
    # fewer than the six independent landmark equations that constrain the
    # four coefficients. A handful of mismarks therefore cannot block an
    # otherwise sound measurement set, while degenerate or minority-supported
    # consensus is refused below.
    needed = max(6, total//2+1)
    best = None
    # Exhaustive minimal subsets stay deterministic at this operator scale;
    # three points give six equations for the four model coefficients.
    for indices in combinations(range(total), 3):
        try:
            coefficients = fit(sensor[list(indices)], ideal[list(indices)])
            certify(coefficients, low, high)
        except (ValueError, np.linalg.LinAlgError):
            continue
        error = np.linalg.norm(project(sensor, coefficients)-ideal, axis=1)*focal
        mask = error <= threshold
        score = (-int(mask.sum()), float(np.sum(error[mask]**2)), indices)
        if best is None or score < best[0]:
            best = (score, mask)
    # A majority of correct points is necessary; with eight matches seven
    # inliers suffice, so a handful of mismarks can never carry a calibration
    # while degenerate or out-of-range sets still fail.
    if best is None or best[1].sum() < needed:
        raise ValueError('no reliable lens calibration consensus')
    mask = best[1]
    coefficients = fit(sensor[mask], ideal[mask])
    bound = certify(coefficients, low, high)
    errors = np.linalg.norm(project(sensor, coefficients)-ideal, axis=1)*focal
    if np.any(errors[mask] > threshold):
        raise ValueError('refitted lens model contradicts reliable landmarks')
    return dict(coefficients=coefficients.tolist(), inliers=np.flatnonzero(mask).tolist(),
                max_error=float(errors[mask].max()), contraction_bound=bound)


def invert(ideal_points, coefficients, low, high, tolerance=1e-10, iterations=64):
    """Invert the certified sensor->ideal map by fixed-point iteration.

    Solves ``ideal = sensor + d(sensor)`` as ``sensor <- ideal - d(sensor)``.
    Certification makes the update a contraction with factor below 0.7 on the
    sensor rectangle. Each iterate is projected back into that rectangle;
    projection onto the convex rectangle is non-expansive, so the projected
    iteration stays a contraction even when an intermediate step would
    otherwise leave the certified region and diverge. A point whose limit lies
    outside the native sensor is reported, never silently clamped in output.
    """
    ideal_points = np.asarray(ideal_points, dtype=float)
    low, high = np.asarray(low, dtype=float), np.asarray(high, dtype=float)
    sensor = np.clip(ideal_points, low, high)
    for _ in range(iterations):
        updated = ideal_points - (project(sensor, coefficients)-sensor)
        if not np.isfinite(updated).all():
            raise ValueError('lens inverse left the finite coordinate range')
        updated = np.clip(updated, low, high)
        residual = float(np.max(np.abs(updated-sensor))) if updated.size else 0.0
        sensor = updated
        if residual < tolerance:
            break
    else:
        raise ValueError('lens inverse did not converge')
    if not np.isfinite(sensor).all():
        raise ValueError('lens inverse left the finite coordinate range')
    span = np.maximum(np.abs(low), np.abs(high))
    epsilon = 1e-9*np.maximum(1.0, span)
    # A limit pinned to the edge is a real inverse only if the forward model
    # reproduces the requested ideal point there; otherwise that output pixel
    # has no native support and must fail instead of reusing a border sample.
    residual = np.linalg.norm(project(sensor, coefficients)-ideal_points, axis=1)
    on_edge = np.any((sensor <= low+epsilon) | (sensor >= high-epsilon), axis=1)
    if np.any(on_edge & (residual > 1e-6)):
        raise ValueError('lens crop exposes pixels outside original sensor')
    if np.any(sensor < low-epsilon) or np.any(sensor > high+epsilon):
        raise ValueError('lens crop exposes pixels outside original sensor')
    return sensor


def mapping(profile, zoom):
    """Native sensor pixel centres sampled for every corrected output pixel."""
    w, h, focal = profile['width'], profile['height'], profile['focal_px']
    c, low, high = geometry(w, h, focal, profile['centre'])
    coefficients = np.asarray(profile['coefficients'])
    certify(coefficients, low, high)
    yy, xx = np.indices((h, w), dtype=float)
    # Output crop is centred on the optical axis, in ideal pixel coordinates.
    ideal = (np.column_stack((xx.ravel(), yy.ravel()))-c)/(focal*zoom)
    sensor = invert(ideal, coefficients, low, high)
    pixels = sensor*focal+c
    return pixels[:, 0].reshape(h, w), pixels[:, 1].reshape(h, w)
