"""Render frozen speed ramps using one materialization per trimmed occurrence."""
from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
from . import audio_signal, media_sources, planner, retime_plan, retime_signal, transition_render, transition_signal
from .assets import fingerprint
from .tools import run


def materialize(plan, directory, stack):
    pictures = []
    audio = []
    frame_bytes = plan['width'] * plan['height'] * 3
    for position, clip in enumerate(plan['clips']):
        output = Path(directory) / f'occurrence-{position}.rgb'
        duration = (clip['end_ms'] - clip['start_ms']) / 1000
        run('ffmpeg', ['-v', 'error', '-y', '-threads', '1', '-filter_threads', '1',
            '-ss', str(clip['start_ms'] / 1000), '-i', clip['path'], '-t', str(duration),
            '-map', '0:v:0', '-an', '-vf',
            'setpts=PTS-STARTPTS,scale=640:360:force_original_aspect_ratio=decrease,'
            'pad=640:360:(ow-iw)/2:(oh-ih)/2,fps=25,setsar=1',
            '-pix_fmt', 'rgb24', '-f', 'rawvideo', str(output)])
        if not output.stat().st_size or output.stat().st_size % frame_bytes:
            raise ValueError('decoder returned incomplete transition video')
        pictures.append({'handle': stack.enter_context(output.open('rb')),
                         'frames': output.stat().st_size // frame_bytes})
        pcm = stack.enter_context(audio_signal.materialized(clip))
        audio.append(stack.enter_context(pcm.open('rb')))
        if fingerprint(clip['path']) != clip['fingerprint']:
            raise ValueError('source changed during transition decoding')
    return pictures, audio


def pictures(plan, sources):
    frame_bytes = plan['width'] * plan['height'] * 3
    if len(sources) != len(plan['clips']):
        raise ValueError('retimed pictures do not match occurrences')
    for index in range(plan['frames']):
        sample = min(index * retime_plan.FRAME_SAMPLES + retime_plan.FRAME_SAMPLES / 2,
                     math_last(plan))
        for clip, source in zip(plan['clips'], sources):
            position = retime_plan.source_position(clip, sample)
            if position is None:
                continue
            local = min(int(position // retime_plan.FRAME_SAMPLES), source['frames'] - 1)
            source['handle'].seek(local * frame_bytes)
            picture = source['handle'].read(frame_bytes)
            if len(picture) != frame_bytes:
                raise ValueError('retimed picture was truncated')
            yield picture
            break
        else:
            raise ValueError('retimed programme contains an uncovered picture')


def math_last(plan):
    return max(0.0, plan['exact_samples'] - 1e-6)


def render(conn, plan_id, destination):
    plan = media_sources.bind(conn, retime_plan.get(conn, plan_id))
    planner.verify_sources(plan)
    destination = Path(destination).expanduser().absolute()
    destination = destination.parent.resolve() / destination.name
    if os.path.lexists(destination) or not destination.parent.is_dir():
        raise ValueError('destination exists or its directory is missing')
    with ExitStack() as stack:
        directory = Path(stack.enter_context(tempfile.TemporaryDirectory(
            prefix='.retime-render-', dir=destination.parent)))
        video_stems, audio_stems = transition_render.materialize(plan, directory, stack)
        raw_video, raw_audio = directory / 'programme.rgb', directory / 'programme.s16'
        with raw_video.open('wb') as handle:
            for picture in pictures(plan, video_stems):
                handle.write(picture)
        with raw_audio.open('wb') as handle:
            for block in retime_signal.audio_blocks(plan, audio_stems):
                handle.write(transition_signal.pcm16(block))
        planner.verify_sources(plan)
        output = directory / 'result.mp4'
        run('ffmpeg', ['-v', 'error', '-y', '-threads', '1', '-filter_threads', '1',
            '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '640x360', '-r', '25',
            '-i', str(raw_video), '-f', 's16le', '-ar', '48000', '-ac', '2',
            '-i', str(raw_audio), '-c:v', 'libx264', '-threads', '1',
            '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', str(output)])
        checksum = fingerprint(output)
        with output.open('rb') as handle:
            os.fsync(handle.fileno())
        os.link(output, destination)
    return {'plan_id': plan_id, 'revision': plan['project']['revision'],
            'output_path': str(destination), 'sha256': checksum,
            'frames': plan['frames'], 'programme_samples': plan['exact_samples'],
            'audio_mode': plan['audio_mode']}
