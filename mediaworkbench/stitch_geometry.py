"""Robust planar registration in native pixel-centre coordinates."""
from itertools import combinations
import numpy as np


def project(matrix, points):
    homogeneous = np.column_stack((points, np.ones(len(points)))) @ matrix.T
    if np.any(np.abs(homogeneous[:, 2]) < 1e-10):
        raise ValueError('registration maps through infinity')
    return homogeneous[:, :2] / homogeneous[:, 2:]


def normalized(points):
    centre = points.mean(axis=0)
    spread = np.sqrt(np.mean(np.sum((points-centre)**2, axis=1)))
    if spread < 1e-8:
        raise ValueError('landmarks have no spatial extent')
    scale = np.sqrt(2) / spread
    matrix = np.array([[scale, 0, -scale*centre[0]], [0, scale, -scale*centre[1]], [0, 0, 1.]])
    return project(matrix, points), matrix


def fit(source, target):
    a, ta = normalized(source)
    b, tb = normalized(target)
    rows = []
    for (x, y), (u, v) in zip(a, b):
        rows.extend(([-x,-y,-1,0,0,0,u*x,u*y,u], [0,0,0,-x,-y,-1,v*x,v*y,v]))
    rows = np.asarray(rows)
    _, singular, vectors = np.linalg.svd(rows, full_matrices=True)
    if np.linalg.matrix_rank(rows, tol=singular[0]*1e-10) < 8:
        raise ValueError('landmarks do not determine a planar registration')
    matrix = np.linalg.inv(tb) @ vectors[-1].reshape(3,3) @ ta
    matrix /= np.linalg.norm(matrix)
    if abs(np.linalg.det(matrix)) < 1e-12:
        raise ValueError('singular planar registration')
    return matrix


def errors(matrix, source, target):
    forward = np.linalg.norm(project(matrix,source)-target, axis=1)
    backward = np.linalg.norm(project(np.linalg.inv(matrix),target)-source, axis=1)
    return np.maximum(forward, backward)


def calibrate(matches, threshold):
    values = np.asarray(matches, dtype=float)
    if values.ndim != 2 or values.shape[1] != 4 or not 6 <= len(values) <= 12 or not np.isfinite(values).all():
        raise ValueError('six to twelve finite [right_x,right_y,left_x,left_y] matches required')
    source, target = values[:,:2], values[:,2:]
    best = None
    # Exhaustive minimal subsets are deterministic and bounded for this operator workflow.
    for indices in combinations(range(len(values)),4):
        try:
            matrix = fit(source[list(indices)],target[list(indices)])
            distances = errors(matrix,source,target)
        except (ValueError,np.linalg.LinAlgError):
            continue
        inliers = distances <= threshold
        score = (-int(inliers.sum()),float(np.sum(distances[inliers]**2)),indices)
        if best is None or score < best[0]:
            best = (score, inliers)
    if best is None or best[1].sum() < max(6, len(values)//2+1):
        raise ValueError('no independently supported planar consensus')
    mask = best[1]
    matrix = fit(source[mask],target[mask])
    distances = errors(matrix,source,target)
    if np.any(distances[mask] > threshold):
        raise ValueError('refitted calibration is inconsistent')
    return dict(matrix=matrix.tolist(), inliers=np.flatnonzero(mask).tolist(),
                max_error=float(distances[mask].max()))


def mapping(matrix, canvas, width, height):
    x, y, w, h = (canvas[k] for k in ('x','y','width','height'))
    yy, xx = np.mgrid[y:y+h,x:x+w]
    world = np.column_stack((xx.ravel(),yy.ravel()))
    right = project(np.linalg.inv(np.asarray(matrix)),world)
    maps=[]
    for points in (world,right):
        px, py = points[:,0].reshape(h,w),points[:,1].reshape(h,w)
        valid=(px>=-1e-7)&(py>=-1e-7)&(px<=width-1+1e-7)&(py<=height-1+1e-7)
        px,py=np.clip(px,0,width-1),np.clip(py,0,height-1)
        edge=np.maximum(1e-6,np.minimum.reduce((px+1,py+1,width-px,height-py)))
        maps.append((px,py,valid,edge))
    if not np.all(maps[0][2]|maps[1][2]):
        raise ValueError('canvas contains pixels outside both cameras')
    return maps
