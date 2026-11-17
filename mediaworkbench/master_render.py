"""One source decode per occurrence, whole-programme mastering and exact auditions."""
from contextlib import ExitStack
import copy
import os
from pathlib import Path
import sys
import tempfile
from . import master_plan, master_signal, mix_render, media_sources
from .assets import fingerprint
from .tools import run


def render(conn, identity, destination, at_sample=0, samples=None, block_frames=4096):
    plan = master_plan.get(conn, identity)
    mix = copy.deepcopy(plan['mix'])
    total = mix['frames']
    samples = total - at_sample if samples is None else samples
    if (type(at_sample) is not int or type(samples) is not int or at_sample < 0
            or samples <= 0 or at_sample + samples > total
            or type(block_frames) is not int or not 1 <= block_frames <= 16384):
        raise ValueError('invalid master audition range or block size')
    target = Path(destination).expanduser().absolute()
    target = target.parent.resolve() / target.name
    if os.path.lexists(target):
        raise ValueError('master destination exists')
    with tempfile.TemporaryDirectory(prefix='.master-', dir=target.parent) as private, ExitStack() as stack:
        root = Path(private)
        for collection in ('beds', 'voices'):
            mix[collection] = [media_sources.select(conn, clip) for clip in mix[collection]]
        stems = mix_render.materialize(mix, stack)
        source = root / 'programme.f32'
        with source.open('wb') as handle:
            for pcm in mix_render.blocks(mix, stems, block_frames):
                if sys.byteorder != 'little':
                    pcm.byteswap()
                handle.write(pcm.tobytes())
        master, metrics = master_signal.master(source, root, plan['target_lufs'], plan['ceiling_db'], block_frames)
        excerpt = root / 'excerpt.f32'
        with master.open('rb') as handle, excerpt.open('wb') as output:
            handle.seek(at_sample * 8)
            remaining = samples * 8
            while remaining:
                chunk = handle.read(min(remaining, 65536))
                if not chunk:
                    raise ValueError('master is incomplete')
                output.write(chunk)
                remaining -= len(chunk)
        output = root / 'delivery.wav'
        run('ffmpeg', ['-v', 'error', '-y', '-f', 'f32le', '-ar', '48000', '-ac', '2',
            '-i', str(excerpt), '-c:a', 'pcm_f32le', str(output)])
        for collection in ('beds', 'voices'):
            for clip in mix[collection]:
                media_sources.select(conn, clip)
        checksum = fingerprint(output)
        with output.open('rb') as handle:
            os.fsync(handle.fileno())
        os.link(output, target)
    return dict(plan_id=identity, path=str(target), sha256=checksum,
        samples=samples, at_sample=at_sample, **metrics)
