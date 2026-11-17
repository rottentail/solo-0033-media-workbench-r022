"""Restore source damage before editorial cropping and protected publication."""
import os
from pathlib import Path
import tempfile
from . import media_sources,repair_plan,repair_signal
from .resample_render import native_pcm
from .assets import fingerprint
from .tools import run


def render(conn,identity,destination,at_sample=0,samples=None,block_frames=4096):
    plan=repair_plan.get(conn,identity)
    samples=plan['samples']-at_sample if samples is None else samples
    if type(at_sample) is not int or type(samples) is not int or at_sample<0 or samples<1 or at_sample+samples>plan['samples']:
        raise ValueError('repair audition outside programme')
    if type(block_frames) is not int or not 1<=block_frames<=65536:
        raise ValueError('invalid repair block size')
    target=Path(destination).expanduser().absolute();target=target.parent.resolve()/target.name
    if os.path.lexists(target) or not target.parent.is_dir():raise ValueError('repair destination exists or directory missing')
    selected=[]
    with tempfile.TemporaryDirectory(prefix='.repair-',dir=target.parent) as directory:
        root=Path(directory);raw=root/'programme.f32';output=root/'programme.wav'
        with raw.open('wb') as handle:
            for occurrence in plan['occurrences']:
                clip=media_sources.select(conn,occurrence['clip']);selected.append(clip)
                with native_pcm(clip,48000,root) as path,path.open('rb') as source:
                    if path.stat().st_size<clip['end_ms']*48*8:raise ValueError('repair audio shorter than source cut')
                    relevant=[p for p in occurrence['marks']['intervals']
                              if p[0]<clip['end_ms']*48 and p[1]>clip['start_ms']*48]
                    restored=repair_signal.patches(source,relevant)
                    first=max(0,at_sample-occurrence['offset'])
                    stop=min(occurrence['samples'],at_sample+samples-occurrence['offset'])
                    for pcm in repair_signal.blocks(source,clip,restored,first,stop,block_frames):handle.write(pcm.tobytes())
        if raw.stat().st_size!=samples*8:raise ValueError('incomplete repair delivery')
        run('ffmpeg',['-v','error','-y','-f','f32le','-ar','48000','-ac','2','-i',str(raw),'-c:a','pcm_f32le',str(output)])
        for clip in selected:media_sources.select(conn,clip)
        checksum=fingerprint(output)
        with output.open('rb') as handle:os.fsync(handle.fileno())
        os.link(output,target)
    return dict(plan_id=identity,path=str(target),samples=samples,at_sample=at_sample,rate=48000,channels=2,sha256=checksum)
