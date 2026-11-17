"""Materialize each selected stream once and composite one picture at a time."""
from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
from . import audio_signal, media_sources, planner, transition_plan, transition_signal
from .assets import fingerprint
from .tools import run


def materialize(plan, directory, stack):
    from .retime_render import materialize as shared_materialize
    return shared_materialize(plan, directory, stack)


def pictures(plan, sources):
    frame_bytes = plan['width'] * plan['height'] * 3
    for frame_index in range(plan['frames']):
        sample = min(frame_index * transition_plan.FRAME_SAMPLES
                     + transition_plan.FRAME_SAMPLES // 2, plan['samples'] - 1)
        coefficients = transition_signal.weights(plan['clips'], sample)
        frames = []
        used = []
        for clip, source, coefficient in zip(plan['clips'], sources, coefficients):
            if coefficient <= 0:
                continue
            local = min((sample - clip['at_sample']) // transition_plan.FRAME_SAMPLES,
                        source['frames'] - 1)
            source['handle'].seek(local * frame_bytes)
            raw = source['handle'].read(frame_bytes)
            if len(raw) != frame_bytes:
                raise ValueError('materialized transition picture was truncated')
            frames.append(raw)
            used.append(coefficient)
        if not frames:
            raise ValueError('transition programme contains an uncovered picture')
        yield transition_signal.picture(frames, used)


def render(conn, plan_id, destination):
    plan = transition_plan.get(conn, plan_id)
    plan = media_sources.bind(conn, plan)
    planner.verify_sources(plan)
    destination = Path(destination).expanduser().absolute()
    destination = destination.parent.resolve() / destination.name
    if os.path.lexists(destination):
        raise ValueError('destination already exists')
    if not destination.parent.is_dir():
        raise ValueError('destination directory does not exist')
    with ExitStack() as stack:
        directory = Path(stack.enter_context(tempfile.TemporaryDirectory(
            prefix='.transition-render-', dir=destination.parent)))
        video_stems, audio_stems = materialize(plan, directory, stack)
        raw_video, raw_audio = directory / 'programme.rgb', directory / 'programme.s16'
        with raw_video.open('wb') as handle:
            for frame in pictures(plan, video_stems):
                handle.write(frame)
        with raw_audio.open('wb') as handle:
            for block in transition_signal.audio_blocks(plan, audio_stems):
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
            'samples': plan['samples'], 'frames': plan['frames'],
            'duration_ms': plan['frames'] * 40}
