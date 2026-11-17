"""Frame-bounded spherical viewport delivery from one original decode."""
import os
from pathlib import Path
import tempfile
import numpy as np
from . import sphere_plan, sphere_geometry, sphere_signal, stitch_render, media_sources
from .assets import fingerprint
from .tools import run


def render(conn, identity, destination, at_frame=0, frame_count=None):
    plan = sphere_plan.get(conn, identity)
    frame_count = plan['frames']-at_frame if frame_count is None else frame_count
    if (type(at_frame) is not int or type(frame_count) is not int
            or not 0 <= at_frame < at_frame+frame_count <= plan['frames']):
        raise ValueError('invalid sphere preview range')
    target = Path(destination).expanduser().absolute()
    target = target.parent.resolve()/target.name
    if os.path.lexists(target) or not target.parent.is_dir():
        raise ValueError('sphere destination exists or parent missing')
    source = media_sources.select(conn, plan['source'])
    rays = sphere_geometry.rays(plan['width'], plan['height'], plan['horizontal_fov'])
    size = source['width']*source['height']*3
    with tempfile.TemporaryDirectory(prefix='.sphere-', dir=target.parent) as directory:
        root = Path(directory)
        spool = root/'source.rgb'
        stitch_render.decode(source, plan['start_ms'], plan['frames'], spool)
        raw = root/'viewport.rgb'
        with spool.open('rb') as handle, raw.open('wb') as out:
            handle.seek(at_frame*size)
            for frame in range(at_frame, at_frame+frame_count):
                data = handle.read(size)
                if len(data) != size:
                    raise ValueError('truncated spherical source spool')
                image = np.frombuffer(data, dtype='uint8').reshape(source['height'], source['width'], 3)
                rotation = sphere_geometry.orientation(plan['keys'], (frame+.5)*40)
                uv = sphere_geometry.coordinates(rays, rotation, source['width'], source['height'])
                out.write(sphere_signal.sample(image, uv).tobytes())
        result = root/'viewport.mkv'
        run('ffmpeg', ['-v','error','-y','-threads','1','-filter_threads','1',
            '-f','rawvideo','-pix_fmt','rgb24','-s',f"{plan['width']}x{plan['height']}",
            '-r','25','-i',str(raw),'-an','-c:v','ffv1','-level','3','-pix_fmt','gbrp',str(result)])
        media_sources.select(conn, source)
        checksum = fingerprint(result)
        with result.open('rb') as handle:
            os.fsync(handle.fileno())
        os.link(result, target)
    return dict(plan_id=identity, path=str(target), at_frame=at_frame, frames=frame_count, sha256=checksum)
