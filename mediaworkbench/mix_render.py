"""Bounded block mixing with one peak compensation for the complete programme."""
from array import array
from contextlib import ExitStack
import hashlib
import math
import os
from pathlib import Path
import sys
import tempfile
import wave
from . import audio_signal, mix_plan, media_sources

BLOCK = 4096


def materialize(plan, stack):
    stems = []
    for role, collection in (('bed', plan['beds']), ('voice', plan['voices'])):
        for clip in collection:
            path = stack.enter_context(audio_signal.materialized(clip))
            handle = stack.enter_context(path.open('rb'))
            frames = audio_signal.samples(clip['end_ms'] - clip['start_ms'])
            if not 0 <= clip['at_sample'] <= plan['frames'] - frames:
                raise ValueError('audio occurrence extends beyond mix extent')
            stems.append({'role': role, 'clip': clip, 'frames': frames, 'handle': handle})
    return stems


def _validate(plan):
    if plan['channels'] != audio_signal.CHANNELS or plan['rate'] != audio_signal.RATE:
        raise ValueError('unsupported mix sample profile')
    points = plan['envelope']
    if (len(points) < 2 or points[0]['sample'] != 0
            or points[-1]['sample'] != plan['frames']):
        raise ValueError('incomplete saved gain envelope')
    for point in points:
        if not math.isfinite(point['gain']) or not 0 <= point['gain'] <= 1:
            raise ValueError('invalid saved gain envelope')
    if any(b['sample'] <= a['sample'] for a, b in zip(points, points[1:])):
        raise ValueError('gain envelope positions must increase')


def blocks(plan, stems, block_frames=BLOCK):
    _validate(plan)
    if type(block_frames) is not int or block_frames < 1:
        raise ValueError('invalid mix block size')
    channels = plan['channels']
    points = plan['envelope']
    segment = 0
    for origin in range(0, plan['frames'], block_frames):
        length = min(block_frames, plan['frames'] - origin)
        background = audio_signal.silence(length)
        foreground = audio_signal.silence(length)
        for stem in stems:
            clip = stem['clip']
            start = max(origin, clip['at_sample'])
            end = min(origin + length, clip['at_sample'] + stem['frames'])
            if start >= end:
                continue
            handle = stem['handle']
            handle.seek((start - clip['at_sample']) * channels * 4)
            pcm = audio_signal.read_frames(handle, end - start)
            if len(pcm) != (end - start) * channels:
                raise ValueError('materialized audio was truncated')
            target = background if stem['role'] == 'bed' else foreground
            offset = (start - origin) * channels
            for index, value in enumerate(pcm):
                target[offset + index] += value * clip['gain']
        output = audio_signal.silence(length)
        for local in range(length):
            sample = origin + local
            while segment + 1 < len(points) - 1 and sample >= points[segment + 1]['sample']:
                segment += 1
            left, right = points[segment:segment + 2]
            fraction = (sample - left['sample']) / (right['sample'] - left['sample'])
            gain = left['gain'] + (right['gain'] - left['gain']) * fraction
            for channel in range(channels):
                index = local * channels + channel
                value = background[index] * gain + foreground[index]
                if not math.isfinite(value):
                    raise ValueError('mix produced a nonfinite sample')
                output[index] = value
        yield output


def encode(pcm, scale):
    encoded = array('h', (max(-32768, min(32767, round(value * scale * 32767)))
                          for value in pcm))
    if sys.byteorder != 'little':
        encoded.byteswap()
    return encoded.tobytes()


def render(conn, plan_id, destination):
    plan = mix_plan.get(conn, plan_id)
    for collection in ('beds', 'voices'):
        plan[collection] = [media_sources.select(conn, clip) for clip in plan[collection]]
    destination = Path(destination).expanduser().absolute()
    destination = destination.parent.resolve() / destination.name
    if os.path.lexists(destination):
        raise ValueError('destination already exists')
    if not destination.parent.is_dir():
        raise ValueError('destination directory does not exist')
    with ExitStack() as stack:
        stems = materialize(plan, stack)
        peak = 0.0
        for pcm in blocks(plan, stems):
            peak = max(peak, max((abs(value) for value in pcm), default=0.0))
        scale = .98 / peak if peak > .98 else 1.0
        directory = stack.enter_context(tempfile.TemporaryDirectory(
            prefix='.audio-mix-', dir=destination.parent))
        output = Path(directory) / 'mix.wav'
        with wave.open(str(output), 'wb') as handle:
            handle.setnchannels(plan['channels'])
            handle.setsampwidth(2)
            handle.setframerate(plan['rate'])
            for pcm in blocks(plan, stems):
                handle.writeframesraw(encode(pcm, scale))
        checksum = hashlib.sha256()
        with output.open('rb') as handle:
            os.fsync(handle.fileno())
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                checksum.update(chunk)
        os.link(output, destination)
    return {'plan_id': plan_id, 'revision': plan['project']['revision'],
            'output_path': str(destination), 'sha256': checksum.hexdigest(),
            'pre_scale_peak': peak, 'output_scale': scale,
            'frames': plan['frames'], 'rate': plan['rate'], 'channels': plan['channels']}
