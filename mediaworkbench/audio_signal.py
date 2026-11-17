"""Decode occurrence-local stereo samples and measure audible foreground spans."""
from array import array
from contextlib import contextmanager
import math
from pathlib import Path
import sys
import tempfile
from .assets import fingerprint
from .tools import run

RATE = 48000
CHANNELS = 2
WINDOW = 960
MAX_SECONDS = 600


def samples(milliseconds):
    if type(milliseconds) is not int or milliseconds < 0:
        raise ValueError('audio time must be a nonnegative integer millisecond')
    return milliseconds * RATE // 1000


def silence(frames):
    return array('f', [0]) * (frames * CHANNELS)


@contextmanager
def materialized(clip, tick=None):
    frames = samples(clip['end_ms'] - clip['start_ms'])
    if frames <= 0 or frames > RATE * MAX_SECONDS:
        raise ValueError('audio occurrence must be between 1 ms and 600 seconds')
    if fingerprint(clip['path'], tick) != clip['fingerprint']:
        raise ValueError('source changed since intake')
    with tempfile.TemporaryDirectory(prefix='media-pcm-') as directory:
        output = Path(directory) / 'samples.f32'
        if clip['has_audio']:
            run('ffmpeg', [
                '-v', 'error', '-y', '-ss', str(clip['start_ms'] / 1000),
                '-i', clip['path'], '-t', str(frames / RATE),
                '-map', '0:a:0', '-vn', '-ac', str(CHANNELS), '-ar', str(RATE),
                '-c:a', 'pcm_f32le', '-f', 'f32le', str(output),
            ], tick=tick)
        else:
            output.touch()
        if output.stat().st_size % (4 * CHANNELS):
            raise ValueError('decoder returned an incomplete audio frame')
        with output.open('r+b') as handle:
            handle.truncate(frames * CHANNELS * 4)
        if fingerprint(clip['path'], tick) != clip['fingerprint']:
            raise ValueError('source changed during audio decoding')
        yield output


def read_frames(handle, frames):
    raw = handle.read(frames * CHANNELS * 4)
    if len(raw) % (4 * CHANNELS):
        raise ValueError('incomplete materialized audio frame')
    pcm = array('f')
    pcm.frombytes(raw)
    if sys.byteorder != 'little':
        pcm.byteswap()
    if any(not math.isfinite(value) for value in pcm):
        raise ValueError('nonfinite decoded audio')
    return pcm


def measure(clip, threshold_db=-38):
    spans = []
    cursor = 0
    with materialized(clip) as path, path.open('rb') as handle:
        while True:
            pcm = read_frames(handle, WINDOW)
            if not pcm:
                break
            for span in activity(pcm, clip['gain'], threshold_db):
                start = cursor + span['start']
                end = cursor + span['end']
                if spans and spans[-1]['end'] == start:
                    spans[-1]['end'] = end
                else:
                    spans.append({'start': start, 'end': end})
            cursor += len(pcm) // CHANNELS
    return spans


def activity(pcm, gain, threshold_db=-38, window=WINDOW):
    if (not math.isfinite(gain) or gain < 0 or not math.isfinite(threshold_db)
            or not -80 <= threshold_db <= 0 or type(window) is not int or window < 1):
        raise ValueError('invalid activity measurement parameters')
    if len(pcm) % CHANNELS:
        raise ValueError('incomplete stereo samples')
    threshold_squared = 10 ** (threshold_db / 10)
    frames = len(pcm) // CHANNELS
    spans = []
    opened = None
    for start in range(0, frames, window):
        end = min(frames, start + window)
        energy = 0.0
        for index in range(start * CHANNELS, end * CHANNELS):
            value = float(pcm[index])
            if not math.isfinite(value):
                raise ValueError('nonfinite activity sample')
            energy += value * value
        audible = energy * gain * gain / ((end - start) * CHANNELS) >= threshold_squared
        if audible and opened is None:
            opened = start
        elif not audible and opened is not None:
            spans.append({'start': opened, 'end': start})
            opened = None
    if opened is not None:
        spans.append({'start': opened, 'end': frames})
    return spans
