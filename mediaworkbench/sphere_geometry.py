"""Spherical camera motion and pixel-centred rectilinear inverse projection.

World axes are right, up, forward. Positive yaw turns right, positive pitch
looks up, positive roll tilts the picture clockwise. Quaternions are xyzw.
"""
import numpy as np
from scipy.spatial.transform import Rotation, Slerp


def orientation(keys, milliseconds):
    times = np.asarray([k['at_ms'] for k in keys], dtype=float)
    quaternions = np.asarray([k['quaternion'] for k in keys], dtype=float)
    return Slerp(times, Rotation.from_quat(quaternions))([milliseconds]).as_matrix()[0]


def rays(width, height, horizontal_fov):
    half = np.tan(np.deg2rad(horizontal_fov) / 2)
    yy, xx = np.mgrid[:height, :width]
    x = (2 * (xx + .5) / width - 1) * half
    y = (1 - 2 * (yy + .5) / height) * half * height / width
    vectors = np.stack((x, y, np.ones_like(x)), axis=-1)
    return vectors / np.linalg.norm(vectors, axis=-1, keepdims=True)


def coordinates(camera_rays, rotation, width, height):
    world = camera_rays @ rotation.T
    longitude = np.arctan2(world[..., 0], world[..., 2])
    latitude = np.arcsin(np.clip(world[..., 1], -1, 1))
    # A source pixel centre lives at ((x+.5)/width, (y+.5)/height).
    x = (longitude / (2*np.pi) + .5) * width - .5
    y = (.5 - latitude / np.pi) * height - .5
    return x, y
