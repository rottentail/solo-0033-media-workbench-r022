"""Simultaneous dissolve coverage, linear-light pixels and continuous audio."""
from array import array
import math
import sys
from . import audio_signal


def weights(clips, sample):
    values = []
    for clip in clips:
        start, stop = clip['at_sample'], clip['stop_sample']
        if not start <= sample < stop:
            values.append(0.0)
            continue
        incoming = min(1.0, (sample - start) / clip['fade_in']) if clip['fade_in'] else 1.0
        outgoing = min(1.0, (stop - sample) / clip['fade_out']) if clip['fade_out'] else 1.0
        values.append(incoming * outgoing)
    total = sum(values)
    if total <= 0:
        return [0.0] * len(values)
    return [value / total for value in values]


def _linear(value):
    value /= 255
    return value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4


def _srgb(value):
    value = value / 65535
    encoded = value * 12.92 if value <= .0031308 else 1.055 * value ** (1 / 2.4) - .055
    return max(0, min(255, round(encoded * 255)))


LINEAR = tuple(_linear(index) for index in range(256))
SRGB = bytes(_srgb(index) for index in range(65536))


def picture(frames, coefficients):
    if len(frames) != len(coefficients) or not frames:
        raise ValueError('picture contributors do not match their coverage')
    extent = len(frames[0])
    if extent % 3 or any(len(frame) != extent for frame in frames):
        raise ValueError('incomplete RGB picture')
    if any(not math.isfinite(value) or value < 0 for value in coefficients):
        raise ValueError('invalid picture coverage')
    if abs(sum(coefficients) - 1) > 1e-8:
        raise ValueError('picture coverage must sum to one')
    if len(frames) == 1:
        return frames[0]
    merged = array('f', [0]) * extent
    for frame, coefficient in zip(frames, coefficients):
        table = [value * coefficient for value in LINEAR]
        for index, value in enumerate(frame):
            merged[index] += table[value]
    return bytes(SRGB[max(0, min(65535, round(value * 65535)))] for value in merged)


def audio_blocks(plan, stems, block_samples=1024):
    if type(block_samples) is not int or block_samples <= 0:
        raise ValueError('audio block extent must be positive')
    clips = plan['clips']
    if len(stems) != len(clips):
        raise ValueError('audio stems do not match transition occurrences')
    for origin in range(0, plan['output_samples'], block_samples):
        count = min(block_samples, plan['output_samples'] - origin)
        sources = []
        for clip, handle in zip(clips, stems):
            start = max(origin, clip['at_sample'])
            stop = min(origin + count, clip['stop_sample'])
            if start >= stop:
                sources.append((0, audio_signal.silence(0)))
                continue
            handle.seek((start - clip['at_sample']) * 8)
            pcm = audio_signal.read_frames(handle, stop - start)
            if len(pcm) != (stop - start) * 2:
                raise ValueError('materialized transition audio was truncated')
            sources.append((start - origin, pcm))
        output = audio_signal.silence(count)
        for local in range(count):
            absolute = origin + local
            if absolute >= plan['samples']:
                break
            coefficients = weights(clips, absolute)
            for clip, (offset, pcm), coefficient in zip(clips, sources, coefficients):
                index = (local - offset) * 2
                if coefficient and 0 <= index < len(pcm):
                    output[local * 2] += pcm[index] * coefficient * clip['gain']
                    output[local * 2 + 1] += pcm[index + 1] * coefficient * clip['gain']
        yield output


def pcm16(samples):
    if any(not math.isfinite(value) for value in samples):
        raise ValueError('nonfinite transition audio')
    encoded = array('h', (max(-32768, min(32767, round(value * 32767))) for value in samples))
    if sys.byteorder != 'little':
        encoded.byteswap()
    return encoded.tobytes()
