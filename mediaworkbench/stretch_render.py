"""Render frozen dialogue timing with one decode and deterministic auditions."""
import json
import os
from pathlib import Path
import tempfile
from . import stretch_plan, stretch_signal, media_sources, audio_signal
from .assets import fingerprint
from .tools import run


def render(conn, identity, destination, at_sample=0, samples=None, block_frames=4096):
    plan = stretch_plan.get(conn, identity)
    samples = plan['samples']-at_sample if samples is None else samples
    if (type(at_sample) is not int or type(samples) is not int or at_sample < 0
            or samples < 1 or at_sample+samples > plan['samples']):
        raise ValueError('dialogue audition outside programme')
    if type(block_frames) is not int or not 1 <= block_frames <= 65536:
        raise ValueError('invalid dialogue block size')
    target = Path(destination).expanduser().absolute()
    target = target.parent.resolve()/target.name
    if os.path.lexists(target) or not target.parent.is_dir():
        raise ValueError('dialogue destination exists or directory missing')
    with tempfile.TemporaryDirectory(prefix='.stretch-', dir=target.parent) as directory:
        root = Path(directory)
        raw, output = root/'dialogue.f32', root/'dialogue.wav'
        selected = []
        with raw.open('wb') as handle:
            for occurrence in plan['occurrences']:
                clip = media_sources.select(conn, occurrence['clip'])
                clip['has_audio'] = any(s.get('codec_type') == 'audio'
                                        for s in json.loads(clip['metadata'])['streams'])
                selected.append(clip)
                cursor = occurrence['offset']
                # Synthesis always starts at the occurrence origin, including auditions.
                with audio_signal.materialized(clip) as source, source.open('rb') as stem:
                    for pcm in stretch_signal.blocks(stem, occurrence, block_frames):
                        end = cursor+len(pcm)
                        left, right = max(cursor, at_sample), min(end, at_sample+samples)
                        if left < right:
                            handle.write(pcm[left-cursor:right-cursor].astype('<f4').tobytes())
                        cursor = end
        if raw.stat().st_size != samples*8:
            raise ValueError('incomplete dialogue output')
        run('ffmpeg', ['-v', 'error', '-y', '-f', 'f32le', '-ar', '48000', '-ac', '2',
                       '-i', str(raw), '-c:a', 'pcm_f32le', str(output)])
        for clip in selected:
            media_sources.select(conn, clip)
        checksum = fingerprint(output)
        with output.open('rb') as handle:
            os.fsync(handle.fileno())
        os.link(output, target)
    return dict(plan_id=identity, path=str(target), samples=samples,
                at_sample=at_sample, rate=48000, channels=2, sha256=checksum)
