"""Subtract frozen reference paths before editorial cropping and gain."""
from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
from . import echo_plan, echo_signal, media_sources
from .resample_render import native_pcm
from .assets import fingerprint
from .tools import run


def render(conn, identity, destination, at_sample=0, samples=None, block_frames=4096):
    plan = echo_plan.get(conn, identity)
    samples = plan['samples']-at_sample if samples is None else samples
    if (type(at_sample) is not int or type(samples) is not int or at_sample<0
            or samples<1 or at_sample+samples>plan['samples']
            or type(block_frames) is not int or not 1<=block_frames<=16384):
        raise ValueError('invalid echo audition or block size')
    target = Path(destination).expanduser().absolute()
    target = target.parent.resolve()/target.name
    if os.path.lexists(target) or not target.parent.is_dir():
        raise ValueError('echo destination exists or directory missing')
    selected = []
    with tempfile.TemporaryDirectory(prefix='.echo-', dir=target.parent) as directory, ExitStack() as stack:
        root = Path(directory)
        raw, output = root/'programme.f32', root/'programme.wav'
        references = {}
        with raw.open('wb') as handle:
            for occurrence in plan['occurrences']:
                p = occurrence['profile']
                key = p['reference']['asset_id']
                if key not in references:
                    ref = media_sources.select(conn, p['reference'])
                    selected.append(ref)
                    path = stack.enter_context(native_pcm(ref, 48000, root))
                    references[key] = (stack.enter_context(path.open('rb')), path.stat().st_size//8)
                rh, rt = references[key]
                clip = media_sources.select(conn, occurrence['clip'])
                selected.append(clip)
                with native_pcm(clip, 48000, root) as path, path.open('rb') as mh:
                    mt = path.stat().st_size//8
                    if clip['end_ms']*48 > mt:
                        raise ValueError('microphone recording shorter than frozen cut')
                    first = max(0, at_sample-occurrence['offset'])
                    stop = min(occurrence['samples'], at_sample+samples-occurrence['offset'])
                    for pcm in echo_signal.blocks(mh, rh, clip, p, first, stop, mt, rt, block_frames):
                        handle.write(pcm.tobytes())
        if raw.stat().st_size != samples*8:
            raise ValueError('incomplete echo delivery')
        run('ffmpeg', ['-v','error','-y','-f','f32le','-ar','48000','-ac','2','-i',str(raw),'-c:a','pcm_f32le',str(output)])
        for clip in selected:
            media_sources.select(conn, clip)
        checksum = fingerprint(output)
        with output.open('rb') as handle:
            os.fsync(handle.fileno())
        os.link(output, target)
    return dict(plan_id=identity, path=str(target), samples=samples, at_sample=at_sample, rate=48000, sha256=checksum)
