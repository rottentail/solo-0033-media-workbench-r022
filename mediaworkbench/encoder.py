"""Normalize independent occurrences before joining compatible streams."""
from pathlib import Path
from .tools import run


def segment(clip, output, tick=None):
    duration = (clip['end_ms'] - clip['start_ms']) / 1000
    args = ['-v', 'error', '-y', '-ss', str(clip['start_ms'] / 1000), '-i', clip['path']]
    if not clip['has_audio']:
        args += ['-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=48000']
    args += [
        '-t', str(duration), '-map', '0:v:0',
        '-map', '0:a:0' if clip['has_audio'] else '1:a:0',
        '-vf', 'scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2,fps=25,setsar=1',
        '-af', f"volume={clip['gain']},aresample=48000,apad,atrim=duration={duration},asetpts=PTS-STARTPTS",
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
        '-ac', '2', '-ar', '48000', '-video_track_timescale', '12800', str(output),
    ]
    run('ffmpeg', args, tick=tick)
    return Path(output)


def join(segments, temporary_dir, tick=None):
    temp = Path(temporary_dir)
    # Link private, fixed names so a cache path never enters concat syntax.
    listing = temp / 'segments.txt'
    entries = []
    for index, path in enumerate(segments):
        name = f'input-{index:06}.mp4'
        (temp / name).symlink_to(Path(path).resolve())
        entries.append(f"file '{name}'\n")
    listing.write_text(''.join(entries), encoding='utf-8')
    output = temp / 'result.mp4'
    run('ffmpeg', ['-v', 'error', '-y', '-f', 'concat', '-safe', '1',
                  '-i', str(listing), '-c', 'copy', '-movflags', '+faststart', str(output)], tick=tick)
    return output


def render(snapshot, temporary_dir):
    temp = Path(temporary_dir)
    segments = [segment(clip, temp / f'{index:06}.mp4')
                for index, clip in enumerate(snapshot['clips'])]
    return join(segments, temp)
