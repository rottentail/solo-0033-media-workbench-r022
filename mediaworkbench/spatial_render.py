"""Decode once and stream a frozen listener trajectory to four-channel float WAV."""
import os
from pathlib import Path
import tempfile
from . import media_sources, planner, spatial_plan, spatial_signal
from .assets import fingerprint
from .tools import run


def write_programme(plan, source, destination, block_frames=1024, start_sample=0, samples=None):
    samples = plan['samples']-start_sample if samples is None else samples
    if type(start_sample) is not int or type(samples) is not int or start_sample < 0 or samples < 1 or start_sample+samples > plan['samples']:
        raise ValueError('audition interval outside programme')
    with source.open('rb') as handle, destination.open('wb') as output:
        handle.seek(start_sample*16)
        for block in spatial_signal.blocks(plan, handle, block_frames, start_sample, samples):
            output.write(block)
    return samples


def render(conn, plan_id, destination, at_ms=0, duration_ms=None):
    plan = media_sources.bind(conn, spatial_plan.get(conn, plan_id))
    count = plan['samples']-at_ms*48 if duration_ms is None else duration_ms*48
    if type(at_ms) is not int or at_ms < 0 or duration_ms is not None and type(duration_ms) is not int or count <= 0 or at_ms*48+count > plan['samples']:
        raise ValueError('audition interval outside programme')
    planner.verify_sources(plan)
    destination = Path(destination).expanduser().absolute()
    destination = destination.parent.resolve()/destination.name
    if os.path.lexists(destination) or not destination.parent.is_dir():
        raise ValueError('destination exists or directory is missing')
    clip = plan['clips'][0]
    with tempfile.TemporaryDirectory(prefix='.spatial-', dir=destination.parent) as directory:
        root = Path(directory)
        raw, transformed, output = root/'input.f32', root/'field.f32', root/'field.wav'
        run('ffmpeg', ['-v','error','-y','-ss',str(clip['start_ms']/1000),'-i',clip['path'],
            '-t',str(plan['samples']/48000),'-map','0:a:0','-vn','-ar','48000',
            '-c:a','pcm_f32le','-f','f32le',str(raw)])
        if raw.stat().st_size != plan['samples']*16:
            raise ValueError('decoder did not preserve the complete four-channel trim')
        write_programme(plan, raw, transformed, start_sample=at_ms*48, samples=count)
        run('ffmpeg', ['-v','error','-y','-f','f32le','-ar','48000','-ac','4','-i',str(transformed),
            '-c:a','pcm_f32le',str(output)])
        planner.verify_sources(plan)
        checksum = fingerprint(output)
        with output.open('rb') as handle:
            os.fsync(handle.fileno())
        os.link(output, destination)
    return {'plan_id': plan_id, 'output_path': str(destination), 'sha256': checksum,
            'samples': count, 'channels': 4, 'convention': 'ACN/SN3D'}
