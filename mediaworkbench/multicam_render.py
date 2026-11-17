"""Stream calibrated camera pictures while preserving the continuous master sound."""
from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
from . import audio_signal, media_sources, multicam_plan, planner, retime_render, sync_clock
from .assets import fingerprint
from .tools import run


def pictures(plan, sources):
    size = plan['width'] * plan['height'] * 3
    if len(sources) != len(plan['clips']):
        raise ValueError('camera materializations do not match the plan')
    for shot in plan['shots']:
        camera = plan['clips'][shot['camera']]
        source = sources[shot['camera']]
        for frame in range(shot['start_frame'], shot['end_frame']):
            position = sync_clock.position(camera, frame*1920+960)
            if position is None:
                raise ValueError('saved shot has no calibrated source coverage')
            local = int(position // 1920)
            if not 0 <= local < source['frames']:
                raise ValueError('calibrated source picture is missing')
            source['handle'].seek(local*size)
            picture = source['handle'].read(size)
            if len(picture) != size:
                raise ValueError('calibrated picture was truncated')
            yield picture


def render(conn, plan_id, destination):
    plan = media_sources.bind(conn, multicam_plan.get(conn, plan_id))
    plan['master'] = media_sources.select(conn, plan['master'])
    planner.verify_sources(dict(clips=plan['clips']+[plan['master']]))
    destination = Path(destination).expanduser().absolute()
    destination = destination.parent.resolve()/destination.name
    if os.path.lexists(destination) or not destination.parent.is_dir():
        raise ValueError('destination exists or directory is missing')
    with ExitStack() as stack:
        directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='.multicam-',dir=destination.parent)))
        # Reuse bounded one-pass disk materialization, independent of selected shots.
        sources, unused_audio = retime_render.materialize(plan, directory, stack)
        raw = directory/'programme.rgb'
        with raw.open('wb') as handle:
            for picture in pictures(plan, sources):
                handle.write(picture)
        master = stack.enter_context(audio_signal.materialized(plan['master']))
        output = directory/'result.mp4'
        run('ffmpeg',['-v','error','-y','-threads','1','-filter_threads','1',
            '-f','rawvideo','-pix_fmt','rgb24','-s','640x360','-r','25','-i',str(raw),
            '-f','f32le','-ar','48000','-ac','2','-i',str(master),
            '-t',str(plan['samples']/48000),'-c:v','libx264','-threads','1',
            '-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart',str(output)])
        planner.verify_sources(dict(clips=plan['clips']+[plan['master']]))
        checksum = fingerprint(output)
        with output.open('rb') as handle:
            os.fsync(handle.fileno())
        os.link(output,destination)
    return {'plan_id':plan_id,'output_path':str(destination),'sha256':checksum,
            'frames':plan['frames'],'shots':plan['shots']}
