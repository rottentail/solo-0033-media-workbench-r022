"""Frame-bounded, linear-light lens correction with lossless publication."""
import os
from pathlib import Path
import tempfile
import numpy as np
from . import lens_plan, lens_geometry, media_sources, stitch_render, rolling_render, stitch_plan
from .assets import fingerprint
from .tools import run


def render(conn, identity, destination, at_frame=0, frame_count=None):
    p = lens_plan.get(conn, identity)
    frame_count = p['frames']-at_frame if frame_count is None else frame_count
    if (not stitch_plan.integer(at_frame, 0, p['frames']-1)
            or not stitch_plan.integer(frame_count, 1, p['frames']-at_frame)):
        raise ValueError('invalid lens preview range')
    target = Path(destination).expanduser().absolute()
    target = target.parent.resolve()/target.name
    if os.path.lexists(target) or not target.parent.is_dir():
        raise ValueError('lens destination exists or parent missing')
    source = media_sources.select(conn, p['source'])
    w, h = source['width'], source['height']
    uv = lens_geometry.mapping(p['profile'], p['zoom'])
    with tempfile.TemporaryDirectory(prefix='.lens-', dir=target.parent) as directory:
        root = Path(directory)
        spool, raw = root/'source.rgb', root/'corrected.rgb'
        stitch_render.decode(source, p['start_ms'], p['frames'], spool)
        with spool.open('rb') as handle, raw.open('wb') as out:
            handle.seek(at_frame*w*h*3)
            for _ in range(frame_count):
                data = handle.read(w*h*3)
                if len(data) != w*h*3:
                    raise ValueError('truncated lens source spool')
                image = np.frombuffer(data, dtype='uint8').reshape(h, w, 3)
                out.write(rolling_render.sample(image, uv).tobytes())
        result = root/'corrected.mkv'
        run('ffmpeg', ['-v','error','-y','-threads','1','-filter_threads','1',
            '-f','rawvideo','-pix_fmt','rgb24','-s',f'{w}x{h}','-r','25','-i',str(raw),
            '-an','-c:v','ffv1','-level','3','-pix_fmt','gbrp',str(result)])
        media_sources.select(conn, source)
        checksum = fingerprint(result)
        with result.open('rb') as handle:
            os.fsync(handle.fileno())
        os.link(result, target)
    return dict(plan_id=identity, path=str(target), at_frame=at_frame,
                frames=frame_count, sha256=checksum)
