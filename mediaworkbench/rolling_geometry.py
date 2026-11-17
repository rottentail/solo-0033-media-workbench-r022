"""Body-clock gyro integration and inverse rolling-shutter projection.

Camera axes: right, down, forward. A pose actively maps camera to world.
Each gyro sample holds a body angular velocity until the next timestamp.
"""
import numpy as np
from scipy.spatial.transform import Rotation


class Trajectory:
    def __init__(self, samples):
        self.times = np.asarray([s['at_ms'] for s in samples], dtype=float)
        self.rates = np.asarray([s['radians_per_second'] for s in samples], dtype=float)
        poses = [Rotation.identity()]
        for i, dt in enumerate(np.diff(self.times) / 1000):
            poses.append(poses[-1] * Rotation.from_rotvec(self.rates[i] * dt))
        self.poses = Rotation.concatenate(poses)

    def at(self, times):
        times = np.asarray(times, dtype=float).reshape(-1)
        if np.any(times < self.times[0]) or np.any(times > self.times[-1]):
            raise ValueError('gyro does not cover sensor exposure')
        indices = np.clip(np.searchsorted(self.times, times, side='right') - 1,
                          0, len(self.times)-2)
        elapsed = (times-self.times[indices]) / 1000
        return self.poses[indices] * Rotation.from_rotvec(self.rates[indices]*elapsed[:, None])


def mapping(trajectory, center_ms, width, height, horizontal_fov, zoom, readout_ms, direction):
    """Find source scanline and therefore exposure time for every output ray.

    For supported rates/readouts/aspects this is a contraction. Check the
    residual, visibility and full footprint rather than publishing an iterate.
    """
    focal = width / (2*np.tan(np.deg2rad(horizontal_fov)/2))
    yy, xx = np.indices((height, width), dtype=float)
    rays = np.stack(((xx+.5-width/2)/(focal*zoom),
                     (yy+.5-height/2)/(focal*zoom), np.ones_like(xx)), axis=-1)
    world = trajectory.at([center_ms]).apply(rays.reshape(-1, 3))
    source_y = (yy+.5-height/2)/zoom + height/2-.5
    sign = 1 if direction == 'top-to-bottom' else -1
    for _ in range(24):
        row_times = center_ms + sign*((source_y.ravel()+.5)/height-.5)*readout_ms
        camera = trajectory.at(row_times).inv().apply(world)
        if np.any(camera[:, 2] <= 0):
            raise ValueError('rolling projection crosses camera horizon')
        projected = focal*camera[:, :2]/camera[:, 2, None]
        x = projected[:, 0].reshape(height, width)+width/2-.5
        y = projected[:, 1].reshape(height, width)+height/2-.5
        residual = float(np.max(np.abs(y-source_y)))
        source_y = y
        if residual < 1e-8:
            break
    else:
        raise ValueError('rolling inverse did not converge')
    tolerance = 1e-7
    if (np.min(x) < -tolerance or np.max(x) > width-1+tolerance
            or np.min(y) < -tolerance or np.max(y) > height-1+tolerance):
        raise ValueError('rolling crop exposes pixels outside original sensor')
    return np.clip(x, 0, width-1), np.clip(y, 0, height-1)
