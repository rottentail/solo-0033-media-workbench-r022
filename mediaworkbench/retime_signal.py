"""Sample stereo stems at continuous source positions without retaining the programme."""
import math
from . import audio_signal, retime_plan


def stereo(handle, position, source_samples):
    left = min(source_samples - 1, max(0, math.floor(position)))
    right = min(source_samples - 1, left + 1)
    handle.seek(left * 8)
    samples = audio_signal.read_frames(handle, right - left + 1)
    if len(samples) != (right - left + 1) * 2:
        raise ValueError('retimed audio stem was truncated')
    fraction = position - left
    return (samples[0] * (1 - fraction) + samples[(right-left)*2] * fraction,
            samples[1] * (1 - fraction) + samples[(right-left)*2+1] * fraction)


def audio_blocks(plan, stems, block_samples=1024):
    if type(block_samples) is not int or block_samples <= 0 or len(stems) != len(plan['clips']):
        raise ValueError('invalid retime audio block or occurrence count')
    for origin in range(0, plan['output_samples'], block_samples):
        count = min(block_samples, plan['output_samples'] - origin)
        output = audio_signal.silence(count)
        for local in range(count):
            absolute = origin + local
            if absolute >= plan['exact_samples']:
                break
            for clip, handle in zip(plan['clips'], stems):
                position = retime_plan.source_position(clip, absolute)
                if position is None:
                    continue
                left, right = stereo(handle, position, clip['source_samples'])
                output[local*2] = left * clip['gain']
                output[local*2+1] = right * clip['gain']
                break
        yield output
