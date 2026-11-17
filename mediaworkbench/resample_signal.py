"""Bounded, zero-phase bandlimited reconstruction on a rational sample clock."""
import math
import numpy as np


def read(handle, first, count, total):
    result = np.zeros((count, 2), dtype=np.float64)
    left, right = max(0, first), min(total, first + count)
    if left < right:
        handle.seek(left * 8)
        raw = handle.read((right-left)*8)
        if len(raw) != (right-left)*8:
            raise ValueError('truncated native audio spool')
        values = np.frombuffer(raw, dtype='<f4').reshape(-1, 2)
        if not np.isfinite(values).all():
            raise ValueError('nonfinite native audio')
        result[left-first:right-first] = values
    return result


def reconstruct(handle, occurrence, rate, first, count, total):
    native = occurrence['native_rate']
    clip = occurrence['clip']
    # Position is calculated from integers, never by accumulating a float step.
    indices = np.arange(first, first+count, dtype=np.int64)
    numerator = indices * native * 1000 + (clip['start_ms']-occurrence['offset_ms']) * native * rate
    denominator = rate * 1000
    centers = numerator // denominator
    fractions = (numerator % denominator) / denominator
    cutoff = min(1.0, rate/native) * .94
    radius = math.ceil(48 / cutoff)
    offsets = np.arange(-radius, radius+1)
    distances = fractions[:, None] - offsets[None, :]
    window = np.i0(9 * np.sqrt(np.maximum(0, 1-(distances/radius)**2))) / np.i0(9)
    window[np.abs(distances) > radius] = 0
    weights = cutoff * np.sinc(cutoff*distances) * window
    weights /= np.sum(weights, axis=1)[:, None]
    taps = centers[:, None] + offsets[None, :]
    left, right = int(taps.min()), int(taps.max())+1
    pcm = read(handle, left, right-left, total)
    # A cut owns only native sample centers inside its source interval. Filter
    # support never borrows speech from the neighbouring, excluded take.
    valid = (taps*1000 >= clip['start_ms']*native) & (taps*1000 < clip['end_ms']*native)
    weights *= valid
    gathered = pcm[taps-left]
    return np.einsum('ij,ijc->ic', weights, gathered, optimize=False) * clip['gain']


def blocks(handle, occurrence, rate, total, first=None, stop=None, block_frames=4096):
    if type(block_frames) is not int or not 1 <= block_frames <= 65536:
        raise ValueError('invalid rate delivery block size')
    first = occurrence['first'] if first is None else max(first, occurrence['first'])
    stop = occurrence['stop'] if stop is None else min(stop, occurrence['stop'])
    for origin in range(first, stop, block_frames):
        count = min(block_frames, stop-origin)
        pieces = []
        for cursor in range(origin, origin+count, 128):
            pieces.append(reconstruct(handle, occurrence, rate, cursor,
                                      min(128, origin+count-cursor), total))
        yield np.concatenate(pieces).astype('<f4')
