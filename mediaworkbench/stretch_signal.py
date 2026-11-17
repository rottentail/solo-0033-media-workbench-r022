"""Bounded stereo waveform alignment with protected speech attacks."""
import numpy as np
from scipy.signal import correlate

WINDOW = 1024
HOP = 256
SEARCH = 192


def read(handle, start, count, total):
    left, right = max(0, start), min(total, start + count)
    result = np.zeros((count, 2), dtype=np.float64)
    if right > left:
        handle.seek(left * 8)
        raw = handle.read((right-left) * 8)
        if len(raw) != (right-left) * 8:
            raise ValueError('truncated dialogue stem')
        values = np.frombuffer(raw, dtype='<f4').reshape(-1, 2)
        if not np.isfinite(values).all():
            raise ValueError('nonfinite dialogue samples')
        result[left-start:right-start] = values
    return result


def attacks(handle, total):
    """Detect a shared stereo energy onset without cancelling antiphase speech."""
    found = []
    previous = 0.0
    last = -2048
    for start in range(0, total, 128):
        pcm = read(handle, start, min(128, total-start), total)
        energy = float(np.mean(pcm * pcm))
        if energy > max(1e-5, previous * 6) and start-last >= 2048:
            found.append(start)
            last = start
        previous = energy
    return found


def timing(knots, onsets):
    """Protect onset neighbourhoods, redistributing stretch between them."""
    pairs = []
    protected = []
    for (s0, t0), (s1, t1) in zip(knots, knots[1:]):
        span = [(float(t0), float(s0))]
        previous = t0
        for source in onsets:
            if not s0+768 <= source <= s1-768:
                continue
            target = t0 + (source-s0) * (t1-t0)/(s1-s0)
            guard = 512
            if target-guard <= previous or target+guard >= t1:
                continue
            span.extend([(target-guard, source-guard), (target+guard, source+guard)])
            protected.append((target-guard, target+guard))
            previous = target+guard
        span.append((float(t1), float(s1)))
        pairs.extend(span if not pairs else span[1:])
    return pairs, protected


def position(points, output):
    for (t0, s0), (t1, s1) in zip(points, points[1:]):
        if output <= t1:
            return s0 + (output-t0) * (s1-s0)/(t1-t0)
    return points[-1][1] + output-points[-1][0]


def align(handle, expected, tail, total, locked):
    """One lag for both channels, selected by normalized stereo correlation."""
    radius = 0 if locked else SEARCH
    candidates = read(handle, expected-radius, WINDOW+radius*2, total)
    if not radius or not np.any(tail):
        return candidates[radius:radius+WINDOW]
    size = len(tail)
    correlations = sum(correlate(candidates[:, c], tail[:, c], mode='valid', method='fft')
                       for c in range(2))[:radius*2+1]
    energy = np.sum(candidates*candidates, axis=1)
    prefix = np.concatenate(([0.0], np.cumsum(energy)))
    energies = prefix[size:size+radius*2+1]-prefix[:radius*2+1]
    scale = np.sqrt(np.maximum(energies * np.sum(tail*tail), 1e-20))
    scores = correlations/scale
    # Prefer the nominal clock in near ties; periodic speech must not drift.
    scores -= np.abs(np.arange(len(scores))-radius) * 1e-7
    lag = int(np.argmax(scores))
    return candidates[lag:lag+WINDOW]


def blocks(handle, occurrence, block_frames=4096):
    if type(block_frames) is not int or not 1 <= block_frames <= 65536:
        raise ValueError('invalid dialogue block size')
    total, length = occurrence['source_samples'], occurrence['output_samples']
    if occurrence['knots'] == [[0, 0], [total, total]]:
        for start in range(0, length, block_frames):
            yield read(handle, start, min(block_frames, length-start), total) * occurrence['clip']['gain']
        return
    mapping, protected = timing(occurrence['knots'], attacks(handle, total))
    weights = np.sin(np.pi*(np.arange(WINDOW)+.5)/WINDOW)**2
    numerator = np.zeros((WINDOW, 2))
    denominator = np.zeros(WINDOW)
    pending = np.empty((0, 2))
    cursor = 0
    # Start before zero so the first audible sample has full overlap support.
    for origin in range(-WINDOW+HOP, length, HOP):
        center = origin+WINDOW//2
        nominal = round(position(mapping, center)-WINDOW//2)
        reference = np.divide(numerator, denominator[:, None],
                              out=np.zeros_like(numerator), where=denominator[:, None]>1e-12)
        locked = any(a-WINDOW//2 <= center <= b+WINDOW//2 for a, b in protected)
        grain = align(handle, nominal, reference[:WINDOW-HOP], total, locked)
        numerator += grain * weights[:, None]
        denominator += weights
        take = min(HOP, length-max(origin, 0)) if origin >= 0 else 0
        if take:
            piece = numerator[:take]/denominator[:take, None]
            pending = np.concatenate((pending, piece * occurrence['clip']['gain']))
            while len(pending) >= block_frames:
                yield pending[:block_frames].astype('<f4')
                pending = pending[block_frames:]
            cursor += take
        numerator[:-HOP] = numerator[HOP:]
        numerator[-HOP:] = 0
        denominator[:-HOP] = denominator[HOP:]
        denominator[-HOP:] = 0
    if cursor != length:
        raise ValueError('dialogue synthesis clock incomplete')
    if len(pending):
        yield pending.astype('<f4')
