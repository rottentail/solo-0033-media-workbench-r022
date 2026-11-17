"""Native sensor-to-ideal polynomial lens calibration and inverse sampling."""
from itertools import combinations
import numpy as np


def basis(points):
    x, y = np.asarray(points, dtype=float).T
    r2 = x*x+y*y
    return np.stack((np.column_stack((x*r2, x*r2*r2, 2*x*y, r2+2*x*x)),
                     np.column_stack((y*r2, y*r2*r2, r2+2*y*y, 2*x*y))), axis=1)


def project(points, coefficients):
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
    # Bound the displacement Jacobian over the entire sensor rectangle.
    # A strict contraction makes the sensor->ideal map injective, and its
    # projected inverse iteration converges even when an iterate leaves it.
    radius = float(np.linalg.norm(np.maximum(np.abs(low), np.abs(high))))
    k1, k2, p1, p2 = np.abs(coefficients)
    bound = 3*k1*radius**2 + 5*k2*radius**4 + 8*(p1+p2)*radius
    if not np.isfinite(bound) or bound >= .7:
        raise ValueError('lens deformation outside certified one-to-one range')
    return float(bound)


def fit(source, target):
    rows = basis(source).reshape(-1, 4)
    coefficients, _, rank, singular = np.linalg.lstsq(rows, (target-source).ravel(), rcond=1e-10)
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
    source, target = (values[:, 2:]-c)/focal, (values[:, :2]-c)/focal
    best = None
    for indices in combinations(range(len(values)), 3):
        try:
            coefficients = fit(source[list(indices)], target[list(indices)])
            certify(coefficients, low, high)
        except (ValueError, np.linalg.LinAlgError):
            continue
        error = np.linalg.norm(project(source, coefficients)-target, axis=1)*focal
        mask = error <= threshold
        score = (-int(mask.sum()), float(np.sum(error[mask]**2)), indices)
        if best is None or score < best[0]:
            best = (score, mask)
    if best is None or best[1].sum() < max(8, len(values)//2+1):
        raise ValueError('no reliable lens calibration consensus')
    mask = best[1]
    coefficients = fit(source[mask], target[mask])
    bound = certify(coefficients, low, high)
    errors = np.linalg.norm(project(source, coefficients)-target, axis=1)*focal
    if np.any(errors[mask] > threshold):
        raise ValueError('refitted lens model contradicts reliable landmarks')
    return dict(coefficients=coefficients.tolist(), inliers=np.flatnonzero(mask).tolist(),
                max_error=float(errors[mask].max()), contraction_bound=bound)


def mapping(profile, zoom):
    w, h, focal = profile['width'], profile['height'], profile['focal_px']
    c, low, high = geometry(w, h, focal, profile['centre'])
    coefficients = np.asarray(profile['coefficients'])
    certify(coefficients, low, high)
    yy, xx = np.indices((h, w), dtype=float)
    # Output crop is centred on the optical axis, in ideal pixel coordinates.
    ideal = (np.column_stack((xx.ravel(), yy.ravel()))-c)/(focal*zoom)
    sensor = project(ideal, coefficients)
    sensor = np.clip(sensor, low, high)
    pixels = sensor*focal+c
    return pixels[:, 0].reshape(h, w), pixels[:, 1].reshape(h, w)
