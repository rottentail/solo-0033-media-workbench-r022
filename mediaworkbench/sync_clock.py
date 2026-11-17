"""Invert calibrated camera clocks on the programme's continuous sample axis."""
from fractions import Fraction
import math

RATE = 48000
FRAME = 1920


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('clock values must be finite numbers')
    return Fraction(str(value))


def calibrate(anchors, duration_ms):
    if not isinstance(anchors, list) or not 2 <= len(anchors) <= 64:
        raise ValueError('a camera needs two to sixty-four clock observations')
    points = [[number(p['programme_ms']) * 48, number(p['source_ms']) * 48]
              for p in anchors]
    for i, (programme, source) in enumerate(points):
        if programme < 0 or not 0 <= source <= number(duration_ms) * 48:
            raise ValueError('clock observation outside media extent')
        if i and (programme <= points[i-1][0] or source <= points[i-1][1]):
            raise ValueError('clock observations must increase on both clocks')
    return [[str(a), str(b)] for a, b in points]


def position(camera, programme_sample):
    points = [(Fraction(a), Fraction(b)) for a, b in camera['clock']]
    t = Fraction(programme_sample)
    if t < points[0][0] or t >= points[-1][0]:
        return None
    for (a, x), (b, y) in zip(points, points[1:]):
        if a <= t < b:
            return x + (t-a) * (y-x) / (b-a)
    return None


def covers(camera, start, end):
    points = camera['clock']
    return Fraction(points[0][0]) <= start and end <= Fraction(points[-1][0])


def score(camera, programme_sample):
    for interval in camera['quality']:
        if interval['start_sample'] <= programme_sample < interval['end_sample']:
            return interval['score']
    return 0
