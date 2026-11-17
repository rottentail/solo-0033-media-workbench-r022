"""Native original decoding and transactional rate-specific WAV publication."""
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
from . import media_sources, resample_plan, resample_signal
from .assets import fingerprint
from .tools import run


@contextmanager
def native_pcm(clip, rate, root):
    with tempfile.TemporaryDirectory(prefix='native-', dir=root) as directory:
        path = Path(directory)/'source.f32'
        run('ffmpeg', ['-v', 'error', '-y', '-i', clip['path'], '-map', '0:a:0',
                       '-vn', '-ac', '2', '-ar', str(rate), '-c:a', 'pcm_f32le',
                       '-f', 'f32le', str(path)])
        if not path.stat().st_size or path.stat().st_size % 8:
            raise ValueError('incomplete native audio decode')
        yield path


def render(conn, identity, destination, at_sample=0, samples=None, block_frames=4096):
    plan = resample_plan.get(conn, identity)
    samples = plan['samples']-at_sample if samples is None else samples
    if (type(at_sample) is not int or type(samples) is not int or at_sample < 0
            or samples < 1 or at_sample+samples > plan['samples']):
        raise ValueError('rate audition outside programme')
    if type(block_frames) is not int or not 1 <= block_frames <= 65536:
        raise ValueError('invalid rate delivery block size')
    target = Path(destination).expanduser().absolute()
    target = target.parent.resolve()/target.name
    if os.path.lexists(target) or not target.parent.is_dir():
        raise ValueError('rate destination exists or directory missing')
    selected = []
    with tempfile.TemporaryDirectory(prefix='.rate-', dir=target.parent) as directory:
        root = Path(directory)
        raw, output = root/'programme.f32', root/'programme.wav'
        with raw.open('wb') as handle:
            for occurrence in plan['occurrences']:
                clip = media_sources.select(conn, occurrence['clip'])
                selected.append(clip)
                # Each occurrence is decoded exactly once at its original rate.
                with native_pcm(clip, occurrence['native_rate'], root) as path, path.open('rb') as source:
                    total = path.stat().st_size//8
                    if occurrence['clip']['end_ms']*occurrence['native_rate'] > total*1000:
                        raise ValueError('native audio shorter than frozen source cut')
                    for pcm in resample_signal.blocks(source, occurrence, plan['rate'], total,
                            at_sample, at_sample+samples, block_frames):
                        handle.write(pcm.tobytes())
        if raw.stat().st_size != samples*8:
            raise ValueError('incomplete rate delivery')
        run('ffmpeg', ['-v', 'error', '-y', '-f', 'f32le', '-ar', str(plan['rate']),
                       '-ac', '2', '-i', str(raw), '-c:a', 'pcm_f32le', str(output)])
        for clip in selected:
            media_sources.select(conn, clip)
        checksum = fingerprint(output)
        with output.open('rb') as handle:
            os.fsync(handle.fileno())
        os.link(output, target)
    return dict(plan_id=identity, path=str(target), rate=plan['rate'], samples=samples,
                at_sample=at_sample, channels=2, sha256=checksum)
