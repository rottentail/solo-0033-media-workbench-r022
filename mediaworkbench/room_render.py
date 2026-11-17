"""Single-decode room delivery with preserved prehistory and complete decay."""
from array import array
import json
import os
from pathlib import Path
import sys
import tempfile
from . import audio_signal, media_sources, room_plan, room_signal
from .assets import fingerprint
from .tools import run


def programme(conn, plan, output):
    selected = []
    for occurrence in plan['snapshot']['clips']:
        clip = media_sources.select(conn,occurrence)
        clip['has_audio'] = any(s.get('codec_type')=='audio' for s in json.loads(clip['metadata'])['streams'])
        selected.append(clip)
        with audio_signal.materialized(clip) as path, path.open('rb') as handle:
            while True:
                pcm = audio_signal.read_frames(handle,4096)
                if not pcm:
                    break
                pcm = array('f',(v*clip['gain'] for v in pcm))
                if sys.byteorder!='little':
                    pcm.byteswap()
                output.write(pcm.tobytes())
    output.flush(); output.seek(0)
    return selected


def render(conn, identity, destination, at_sample=0, samples=None, block_frames=4096):
    plan = room_plan.get(conn,identity)
    total = plan['frames']+max(s['response']['frames'] for s in plan['stages'])-1
    samples = total-at_sample if samples is None else samples
    if type(at_sample) is not int or type(samples) is not int or at_sample<0 or samples<1 or at_sample+samples>total:
        raise ValueError('room audition outside complete programme and decay')
    target = Path(destination).expanduser().absolute()
    target = target.parent.resolve()/target.name
    if os.path.lexists(target) or not target.parent.is_dir():
        raise ValueError('room destination exists or directory missing')
    with tempfile.TemporaryDirectory(prefix='.room-',dir=target.parent) as directory:
        root = Path(directory)
        raw, output = root/'room.f32', root/'room.wav'
        with tempfile.TemporaryFile() as spool, raw.open('wb') as handle:
            selected = programme(conn,plan,spool)
            cursor = 0
            for pcm in room_signal.blocks(spool,plan,block_frames):
                end = cursor+len(pcm)//2
                left,right = max(cursor,at_sample),min(end,at_sample+samples)
                if left<right:
                    piece = pcm[(left-cursor)*2:(right-cursor)*2]
                    if sys.byteorder!='little':
                        piece.byteswap()
                    handle.write(piece.tobytes())
                cursor = end
        if raw.stat().st_size!=samples*8:
            raise ValueError('incomplete room output')
        run('ffmpeg',['-v','error','-y','-f','f32le','-ar','48000','-ac','2','-i',str(raw),
            '-c:a','pcm_f32le',str(output)])
        for clip in selected:
            media_sources.select(conn,clip)
        checksum = fingerprint(output)
        with output.open('rb') as handle:
            os.fsync(handle.fileno())
        os.link(output,target)
    return dict(plan_id=identity,path=str(target),sha256=checksum,samples=samples,rate=48000,channels=2)
