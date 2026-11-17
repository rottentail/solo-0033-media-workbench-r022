"""Bounded single-source decoding and sample-exact frozen music assembly."""
from array import array
from contextlib import ExitStack
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from . import audio_signal, media_sources, music_plan
from .assets import fingerprint
from .tools import run


def blocks(handles, plan, start, stop, block_frames):
    sequence = plan['sequence']
    overlap = plan['overlap_ms']*48
    for cursor in range(start,stop,block_frames):
        frames = min(block_frames,stop-cursor)
        output = audio_signal.silence(frames)
        for index, phrase in enumerate(sequence):
            at, duration = phrase['at_ms']*48,phrase['duration_ms']*48
            left,right = max(cursor,at),min(cursor+frames,at+duration)
            if left>=right:
                continue
            handle = handles[phrase['asset_id']]
            handle.seek((phrase['start_ms']*48+left-at)*8)
            pcm = audio_signal.read_frames(handle,right-left)
            if len(pcm)!=(right-left)*2:
                raise ValueError('music source spool is incomplete')
            for local in range(right-left):
                position = left+local-at
                weight = 1.0
                if overlap and index and position<overlap:
                    weight *= math.sin(math.pi*position/(2*overlap))
                if overlap and index<len(sequence)-1 and position>=duration-overlap:
                    weight *= math.cos(math.pi*(position-duration+overlap)/(2*overlap))
                factor = phrase['gain']*weight
                offset = (left-cursor+local)*2
                output[offset] += pcm[local*2]*factor
                output[offset+1] += pcm[local*2+1]*factor
        yield output


def render(conn, identity, destination, at_sample=0, samples=None, block_frames=4096):
    plan = music_plan.get(conn,identity)
    total = plan['duration_ms']*48
    samples = total-at_sample if samples is None else samples
    if (type(at_sample) is not int or type(samples) is not int or at_sample<0 or samples<1
            or at_sample+samples>total or type(block_frames) is not int or not 1<=block_frames<=16384):
        raise ValueError('invalid music audition range or block size')
    target = Path(destination).expanduser().absolute()
    target = target.parent.resolve()/target.name
    if os.path.lexists(target) or not target.parent.is_dir():
        raise ValueError('music destination exists or directory is missing')
    with tempfile.TemporaryDirectory(prefix='.music-',dir=target.parent) as private, ExitStack() as stack:
        root = Path(private)
        raw, output = root/'music.f32',root/'music.wav'
        handles, selected = {}, []
        for phrase in plan['sequence']:
            if phrase['asset_id'] in handles:
                continue
            original = dict(phrase,start_ms=0,end_ms=round(float(json.loads(phrase['metadata'])['format']['duration'])*1000),has_audio=True)
            clip = media_sources.select(conn,original)
            selected.append(clip)
            path = stack.enter_context(audio_signal.materialized(clip))
            handles[phrase['asset_id']] = stack.enter_context(path.open('rb'))
        with raw.open('wb') as handle:
            for pcm in blocks(handles,plan,at_sample,at_sample+samples,block_frames):
                if sys.byteorder!='little':
                    pcm.byteswap()
                handle.write(pcm.tobytes())
        if raw.stat().st_size!=samples*8:
            raise ValueError('incomplete music assembly')
        run('ffmpeg',['-v','error','-y','-f','f32le','-ar','48000','-ac','2','-i',str(raw),'-c:a','pcm_f32le',str(output)])
        for clip in selected:
            media_sources.select(conn,clip)
        checksum = fingerprint(output)
        with output.open('rb') as handle:
            os.fsync(handle.fileno())
        os.link(output,target)
    return dict(plan_id=identity,path=str(target),sha256=checksum,samples=samples,score=plan['score'])
