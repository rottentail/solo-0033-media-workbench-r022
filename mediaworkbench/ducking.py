"""Construct the lower envelope of overlapping foreground attenuation ramps."""
from bisect import bisect_right
import math


def _curve(span, sample):
    start, end, gain, attack, release = span
    if sample < start:
        if not attack or sample <= start - attack:
            return 1.0
        return 1 + (gain - 1) * (sample - start + attack) / attack
    if sample <= end:
        return gain
    if not release or sample >= end + release:
        return 1.0
    return gain + (1 - gain) * (sample - end) / release


def envelope(voices, frames, attack, release):
    if type(frames) is not int or frames <= 0 or attack < 0 or release < 0:
        raise ValueError('invalid envelope extent')
    curves = []
    boundaries = {0, frames}
    for voice in voices:
        offset = voice['at_sample']
        gain = voice['duck_gain']
        if not math.isfinite(gain) or not 0 <= gain <= 1:
            raise ValueError('invalid attenuation gain')
        for span in voice['activity']:
            start = offset + span['start']
            end = offset + span['end']
            if not 0 <= start < end <= frames:
                raise ValueError('activity extends beyond the frozen mix')
            curves.append((start, end, gain, attack, release))
            for point in (start - attack, start, end, end + release):
                if 0 < point < frames:
                    boundaries.add(point)
    if len(curves) > 512:
        raise ValueError('mix has more than 512 audible spans')
    if not curves:
        return [{'sample': 0, 'gain': 1.0}, {'sample': frames, 'gain': 1.0}]
    knots = sorted(boundaries)
    points = []
    for left, right in zip(knots, knots[1:]):
        lines = [(1.0, 1.0)]
        for curve in curves:
            if curve[0] - attack <= right and curve[1] + release >= left:
                lines.append((_curve(curve, left), _curve(curve, right)))
        crossings = {float(left), float(right)}
        for index, first in enumerate(lines):
            for second in lines[index + 1:]:
                delta_left = first[0] - second[0]
                delta_right = first[1] - second[1]
                if delta_left * delta_right < 0:
                    fraction = delta_left / (delta_left - delta_right)
                    crossings.add(left + fraction * (right - left))
        for position in sorted(crossings):
            fraction = (position - left) / (right - left)
            value = min(a + (b - a) * fraction for a, b in lines)
            point = {'sample': position, 'gain': value}
            if points and abs(points[-1]['sample'] - position) < 1e-8:
                points[-1] = point
            else:
                points.append(point)
            while len(points) >= 3:
                a, b, c = points[-3:]
                area = ((b['gain'] - a['gain']) * (c['sample'] - b['sample'])
                        - (c['gain'] - b['gain']) * (b['sample'] - a['sample']))
                if abs(area) > 1e-9:
                    break
                points.pop(-2)
    return points


def value_at(points, sample):
    if not points or sample < points[0]['sample'] or sample > points[-1]['sample']:
        raise ValueError('sample is outside the envelope')
    index = min(bisect_right([p['sample'] for p in points], sample) - 1, len(points) - 2)
    left, right = points[index:index + 2]
    fraction = (sample - left['sample']) / (right['sample'] - left['sample'])
    return left['gain'] + (right['gain'] - left['gain']) * fraction
