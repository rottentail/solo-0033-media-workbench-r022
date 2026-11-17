"""Bounded decoded spools and lossless planar panorama delivery."""
import os
from pathlib import Path
import tempfile
import numpy as np
from . import stitch_plan, stitch_geometry, stitch_signal, media_sources
from .assets import fingerprint
from .tools import run


def decode(clip, start_ms, frames, destination):
    run('ffmpeg',['-v','error','-y','-threads','1','-filter_threads','1',
        '-ss',str(start_ms/1000),'-i',clip['path'],'-an','-vf','fps=25',
        '-frames:v',str(frames),'-pix_fmt','rgb24','-f','rawvideo',str(destination)])
    expected=clip['width']*clip['height']*3*frames
    if destination.stat().st_size!=expected:
        raise ValueError('stitch camera did not provide every planned frame')


def render(conn, identity, destination, at_frame=0, frame_count=None):
    plan=stitch_plan.get(conn,identity)
    frame_count=plan['frames']-at_frame if frame_count is None else frame_count
    if not stitch_plan.integer(at_frame,0,plan['frames']-1) or not stitch_plan.integer(frame_count,1,plan['frames']-at_frame):
        raise ValueError('invalid stitch preview range')
    p,canvas=plan['profile'],plan['canvas']
    target=Path(destination).expanduser().absolute()
    target=target.parent.resolve()/target.name
    if os.path.lexists(target) or not target.parent.is_dir():
        raise ValueError('stitch destination exists or directory missing')
    maps=stitch_geometry.mapping(p['matrix'],canvas,p['left']['width'],p['left']['height'])
    selected=[media_sources.select(conn,p[k]) for k in ('left','right')]
    with tempfile.TemporaryDirectory(prefix='.stitch-',dir=target.parent) as directory:
        root=Path(directory)
        paths=[root/'left.rgb',root/'right.rgb']
        for which,clip,path in zip(('left','right'),selected,paths):
            decode(clip,plan[which+'_start_ms'],plan['frames'],path)
        size=p['left']['width']*p['left']['height']*3
        raw=root/'panorama.rgb'
        with paths[0].open('rb') as lh,paths[1].open('rb') as rh,raw.open('wb') as out:
            lh.seek(at_frame*size)
            rh.seek(at_frame*size)
            for _ in range(frame_count):
                pictures=[]
                for handle in (lh,rh):
                    data=handle.read(size)
                    if len(data)!=size:
                        raise ValueError('truncated stitch camera spool')
                    pictures.append(np.frombuffer(data,dtype='uint8').reshape(p['left']['height'],p['left']['width'],3))
                out.write(stitch_signal.compose(*pictures,maps).tobytes())
        result=root/'panorama.mkv'
        run('ffmpeg',['-v','error','-y','-threads','1','-filter_threads','1','-f','rawvideo',
            '-pix_fmt','rgb24','-s',f"{canvas['width']}x{canvas['height']}",'-r','25','-i',str(raw),
            '-an','-c:v','ffv1','-level','3','-pix_fmt','gbrp',str(result)])
        for clip in selected:
            media_sources.select(conn,clip)
        checksum=fingerprint(result)
        with result.open('rb') as handle:
            os.fsync(handle.fileno())
        os.link(result,target)
    return dict(plan_id=identity,path=str(target),at_frame=at_frame,frames=frame_count,sha256=checksum)
