"""Gated programme energy and bounded, linked true-peak gain trajectories."""
from pathlib import Path
import math
import numpy as np
from scipy.signal import lfilter
from .tools import run

RATE = 48000
QUANTUM = 480


def _weighted(path, root):
    result = Path(root) / 'weighted.f32'
    state_a = np.zeros((2, 2))
    state_b = np.zeros((2, 2))
    with open(path, 'rb') as source, result.open('wb') as target:
        while raw := source.read(4096 * 8):
            pcm = np.frombuffer(raw, dtype='<f4').reshape(-1, 2).astype(float)
            first, state_a = lfilter([1.53512485958697, -2.69169618940638, 1.19839281085285],
                [1, -1.69065929318241, .73248077421585], pcm, axis=0, zi=state_a)
            second, state_b = lfilter([1, -2, 1],
                [1, -1.99004745483398, .99007225036621], first, axis=0, zi=state_b)
            target.write(second.astype('<f4').tobytes())
    return result


def integrated(path, root):
    weighted = _weighted(path, root)
    frames = weighted.stat().st_size // 8
    if frames < 19200:
        raise ValueError('master programme must contain at least 400 ms')
    energies = []
    with weighted.open('rb') as handle:
        for origin in range(0, frames - 19200 + 1, 4800):
            handle.seek(origin * 8)
            pcm = np.frombuffer(handle.read(19200 * 8), dtype='<f4').reshape(-1, 2)
            energies.append(float(np.mean(np.sum(pcm.astype(float) ** 2, axis=1))))
    energy = np.asarray(energies)
    absolute = energy[energy > 10 ** ((-70 + .691) / 10)]
    if not len(absolute):
        return None
    relative = float(np.mean(absolute)) / 10
    admitted = absolute[absolute >= relative]
    return -.691 + 10 * math.log10(float(np.mean(admitted)))


def peaks(path, root):
    oversampled = Path(root) / 'oversampled.f32'
    run('ffmpeg', ['-v', 'error', '-y', '-f', 'f32le', '-ar', '48000', '-ac', '2',
        '-i', str(path), '-af', 'aresample=192000:filter_size=64:phase_shift=10',
        '-f', 'f32le', str(oversampled)])
    values = []
    with oversampled.open('rb') as handle:
        while raw := handle.read(QUANTUM * 4 * 8):
            values.append(float(np.max(np.abs(np.frombuffer(raw, dtype='<f4')))))
    return np.asarray(values)


def trajectory(peak, gain, ceiling):
    allowed = np.minimum(gain, ceiling / np.maximum(peak, 1e-20))
    # Include neighbouring cells: interpolation and resampling straddle boundaries.
    guarded = allowed.copy()
    for shift in range(1, 4):
        guarded[:-shift] = np.minimum(guarded[:-shift], allowed[shift:])
        guarded[shift:] = np.minimum(guarded[shift:], allowed[:-shift])
    envelope = guarded.copy()
    step = 10 ** (.25 / 20)
    for index in range(len(envelope) - 2, -1, -1):
        envelope[index] = min(envelope[index], envelope[index + 1] * step)
    for index in range(1, len(envelope)):
        envelope[index] = min(envelope[index], envelope[index - 1] * step)
    return envelope


def apply(source, destination, envelope, block_frames=4096):
    cursor = 0
    positions = np.arange(len(envelope)) * QUANTUM
    with open(source, 'rb') as handle, open(destination, 'wb') as output:
        while raw := handle.read(block_frames * 8):
            pcm = np.frombuffer(raw, dtype='<f4').reshape(-1, 2)
            indices = np.arange(cursor, cursor + len(pcm))
            gains = np.interp(indices, positions, envelope)
            output.write((pcm * gains[:, None]).astype('<f4').tobytes())
            cursor += len(pcm)


def master(source, root, target_lufs, ceiling_db, block_frames=4096):
    measured = integrated(source, root)
    if measured is None:
        raise ValueError('silent programme has no measurable loudness')
    peak = peaks(source, root)
    ceiling = 10 ** (ceiling_db / 20)
    output = Path(root) / 'master.f32'
    low, high = -40., 40.
    best = None
    for _ in range(18):
        db = (low + high) / 2
        envelope = trajectory(peak, 10 ** (db / 20), ceiling * .985)
        apply(source, output, envelope, block_frames)
        loudness = integrated(output, root)
        if loudness is None:
            low = db
            continue
        if best is None or abs(loudness - target_lufs) < best[0]:
            best = (abs(loudness - target_lufs), db, loudness)
        if loudness < target_lufs:
            low = db
        else:
            high = db
    if best is None or best[0] > .15:
        raise ValueError('target loudness is infeasible under the true-peak ceiling')
    envelope = trajectory(peak, 10 ** (best[1] / 20), ceiling * .985)
    apply(source, output, envelope, block_frames)
    final_lufs = integrated(output, root)
    final_peak = float(np.max(peaks(output, root)))
    if abs(final_lufs - target_lufs) > .15 or final_peak > ceiling * 1.001:
        raise ValueError('master does not meet programme loudness and true-peak limits')
    return output, dict(integrated_lufs=final_lufs,
        true_peak_db=20 * math.log10(max(final_peak, 1e-20)),
        input_lufs=measured, gain_db=best[1])
