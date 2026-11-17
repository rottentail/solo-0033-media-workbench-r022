"""Decode each occurrence once, analyse its full shot and stream display frames."""
import os
from pathlib import Path
import tempfile
import numpy as np
from . import color_plan, color_signal, media_sources, planner
from .assets import fingerprint
from .tools import run


def materialize(clip, root, index, width, height):
    path = root / f'source-{index}.rgb48'
    matrix = 'bt709' if clip['encoding'] == 'srgb709' else 'bt2020'
    run('ffmpeg', ['-v', 'error', '-y', '-threads', '1', '-filter_threads', '1',
        '-ss', str(clip['start_ms'] / 1000), '-i', clip['path'],
        '-t', str((clip['end_ms'] - clip['start_ms']) / 1000), '-an',
        '-vf', f'scale={width}:{height}:in_color_matrix={matrix}:out_range=full,fps=25', '-frames:v', str(clip['frames']),
        '-pix_fmt', 'rgb48le', '-f', 'rawvideo', str(path)])
    if path.stat().st_size != width * height * 6 * clip['frames']:
        raise ValueError('colour source did not provide every planned picture')
    return path


def frames(path, clip, width, height):
    count = width * height * 6
    with path.open('rb') as handle:
        while raw := handle.read(count):
            if len(raw) != count:
                raise ValueError('truncated high precision picture')
            rgb = np.frombuffer(raw, dtype='<u2').reshape(height, width, 3) / 65535
            yield color_signal.decode(rgb, clip['encoding'])


def render(conn, identity, destination, at_frame=0, frame_count=None):
    plan = color_plan.get(conn, identity)
    frame_count = plan['frames'] - at_frame if frame_count is None else frame_count
    if (type(at_frame) is not int or type(frame_count) is not int
            or at_frame < 0 or frame_count < 1 or at_frame + frame_count > plan['frames']):
        raise ValueError('invalid colour preview range')
    snapshot = media_sources.bind(conn, plan['snapshot'])
    planner.verify_sources(snapshot)
    target = Path(destination).expanduser().absolute()
    target = target.parent.resolve() / target.name
    if os.path.lexists(target):
        raise ValueError('colour destination exists')
    with tempfile.TemporaryDirectory(prefix='.color-', dir=target.parent) as directory:
        root = Path(directory)
        raw_output = root / 'display.rgb'
        cursor = 0
        gains = []
        with raw_output.open('wb') as output:
            for index, clip in enumerate(snapshot['clips']):
                source = materialize(clip, root, index, plan['width'], plan['height'])
                histogram = np.zeros(1024, dtype=np.int64)
                for linear in frames(source, clip, plan['width'], plan['height']):
                    color_signal.accumulate(histogram, linear)
                gain = color_signal.exposure(histogram, plan['white_nits'])
                gains.append(gain)
                for local, linear in enumerate(frames(source, clip, plan['width'], plan['height'])):
                    if at_frame <= cursor + local < at_frame + frame_count:
                        display = color_signal.encode(color_signal.map_frame(linear, gain, plan['white_nits']))
                        output.write(np.rint(display * 255).astype('uint8').tobytes())
                cursor += clip['frames']
                source.unlink()
        result = root / 'delivery.mkv'
        run('ffmpeg', ['-v', 'error', '-y', '-threads', '1', '-filter_threads', '1',
            '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f"{plan['width']}x{plan['height']}",
            '-r', '25', '-i', str(raw_output), '-an', '-c:v', 'ffv1', '-level', '3',
            '-pix_fmt', 'gbrp', '-colorspace', 'bt709', '-color_primaries', 'bt709',
            '-color_trc', 'iec61966-2-1', '-color_range', 'pc', str(result)])
        planner.verify_sources(snapshot)
        checksum = fingerprint(result)
        with result.open('rb') as handle:
            os.fsync(handle.fileno())
        os.link(result, target)
    return dict(plan_id=identity, path=str(target), frames=frame_count,
                at_frame=at_frame, gains=gains, sha256=checksum)
