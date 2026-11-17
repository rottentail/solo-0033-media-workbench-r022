"""Render a frozen restoration or its exact sample slice, with bounded memory."""
from array import array
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import wave
from . import audio_signal, denoise_plan, denoise_signal, media_sources


def programme(conn, plan, output):
    # Each occurrence decodes once. The disk spool unifies the programme clock
    # without retaining decoded media in RAM or resetting a filter at each cut.
    for occurrence in plan['snapshot']['clips']:
        clip = media_sources.select(conn, occurrence)
        clip['has_audio'] = any(s.get('codec_type') == 'audio' for s in json.loads(clip['metadata'])['streams'])
        with audio_signal.materialized(clip) as path, path.open('rb') as handle:
            while True:
                pcm = audio_signal.read_frames(handle, 4096)
                if not pcm:
                    break
                if sys.byteorder != 'little':
                    pcm.byteswap()
                output.write(pcm.tobytes())
    output.flush()
    output.seek(0)


def render(conn, plan_id, destination, at_ms=0, duration_ms=None, block_frames=4096):
    plan = denoise_plan.get(conn, plan_id)
    frames = sum(audio_signal.samples(c['end_ms']-c['start_ms']) for c in plan['snapshot']['clips'])
    if duration_ms is None:
        duration_ms = frames//48-at_ms
    if (type(at_ms) is not int or type(duration_ms) is not int or at_ms < 0
            or duration_ms <= 0 or (at_ms+duration_ms)*48 > frames):
        raise ValueError('restoration audition must lie within the programme')
    start, stop = at_ms*48, (at_ms+duration_ms)*48
    target = Path(destination).expanduser().absolute()
    target = target.parent.resolve()/target.name
    if os.path.lexists(target) or not target.parent.is_dir():
        raise ValueError('restoration destination exists or directory is missing')
    settings = plan['settings']
    floor = 10**(settings['floor_db']/20)
    release = math.exp(-denoise_signal.HOP/(48*settings['release_ms']))
    gains = []
    extent = 0
    for clip in plan['snapshot']['clips']:
        extent += audio_signal.samples(clip['end_ms']-clip['start_ms'])
        gains.append((extent, clip['gain']))
    with tempfile.TemporaryDirectory(prefix='.restoration-', dir=target.parent) as private:
        output = Path(private)/'restored.wav'
        with tempfile.TemporaryFile() as source:
            programme(conn, plan, source)
            with wave.open(str(output), 'wb') as handle:
                handle.setnchannels(2)
                handle.setsampwidth(2)
                handle.setframerate(48000)
                cursor = 0
                occurrence = 0
                for pcm in denoise_signal.blocks(source, frames, plan['profile']['power'],
                        settings['strength'], floor, release, block_frames):
                    length = len(pcm)//2
                    left, right = max(start,cursor), min(stop,cursor+length)
                    if left < right:
                        selected = pcm[(left-cursor)*2:(right-cursor)*2]
                        for local in range(right-left):
                            while left+local >= gains[occurrence][0]:
                                occurrence += 1
                            for channel in range(2):
                                selected[local*2+channel] *= gains[occurrence][1]
                        encoded = array('h', (max(-32768,min(32767,round(value*32767))) for value in selected))
                        if sys.byteorder != 'little':
                            encoded.byteswap()
                        handle.writeframesraw(encoded.tobytes())
                    cursor += length
        checksum = hashlib.sha256()
        with output.open('rb') as handle:
            os.fsync(handle.fileno())
            for chunk in iter(lambda: handle.read(1048576), b''):
                checksum.update(chunk)
        os.link(output, target)
    return dict(plan_id=plan_id, revision=plan['snapshot']['project']['revision'],
                path=str(target), sha256=checksum.hexdigest(), frames=stop-start, rate=48000, channels=2)
