"""Linear-light interpolation on the wrapped equirectangular sphere."""
import numpy as np
from .stitch_signal import linear, encoded


def texels(frame, x, y):
    height, width = frame.shape[:2]
    # Continuing over either pole reflects latitude and advances longitude
    # half a revolution. Only the nearest halo is needed by bilinear sampling.
    below, above = y < 0, y >= height
    reflected = below | above
    y = np.where(below, -y-1, np.where(above, 2*height-y-1, y))
    x = (x + reflected * (width//2)) % width
    return linear(frame[y, x])


def sample(frame, coordinates):
    x, y = coordinates
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    dx, dy = (x-x0)[..., None], (y-y0)[..., None]
    top = texels(frame, x0, y0)*(1-dx) + texels(frame, x0+1, y0)*dx
    bottom = texels(frame, x0, y0+1)*(1-dx) + texels(frame, x0+1, y0+1)*dx
    return encoded(top*(1-dy) + bottom*dy)
