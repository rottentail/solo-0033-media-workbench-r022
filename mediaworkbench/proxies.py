"""Publish immutable audition proxies while preserving original-only delivery."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import uuid
from . import db, encoder, media_sources, planner, timeline, proxy_store
from .assets import fingerprint
from .tools import run


def create(conn, asset_id, directory):
    asset = media_sources.get(conn, asset_id)
    directory = Path(directory).expanduser().resolve(strict=True)
    if not directory.is_dir():
        raise ValueError('proxy store must be an existing directory')
    # Reserve only publication precedence, leaving a previous good proxy usable.
    with db.write(conn):
        conn.execute('INSERT INTO asset_proxies(asset_id,generation) VALUES (?,1) '
            'ON CONFLICT(asset_id) DO UPDATE SET generation=generation+1,error=NULL', (asset_id,))
        generation = media_sources.get(conn, asset_id)['proxy']['generation']
    published = None
    accepted = False
    try:
        original = media_sources.select(conn, {'asset_id': asset_id,
            'fingerprint': asset['fingerprint']}, 'final')
        metadata = json.loads(original['metadata'])
        if not any(s.get('codec_type') == 'video' for s in metadata['streams']):
            raise ValueError('preview proxies require video')
        duration = float(metadata['format']['duration'])
        with tempfile.TemporaryDirectory(prefix='.proxy-build-', dir=directory) as temporary:
            output = Path(temporary) / 'proxy.mp4'
            args = ['-v', 'error', '-y', '-i', original['path']]
            has_audio = any(s.get('codec_type') == 'audio' for s in metadata['streams'])
            if not has_audio:
                args += ['-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=48000']
            args += ['-t', str(duration), '-map', '0:v:0',
                '-map', '0:a:0' if has_audio else '1:a:0',
                '-vf', 'scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:(ow-iw)/2:(oh-ih)/2,fps=25,setsar=1',
                '-af', f'aresample=48000,apad,atrim=duration={duration},asetpts=PTS-STARTPTS',
                '-c:v', 'libx264', '-crf', '32', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', '-ac', '2', '-ar', '48000', '-movflags', '+faststart', str(output)]
            run('ffmpeg', args)
            if fingerprint(original['path']) != asset['fingerprint']:
                raise ValueError('original changed while preparing its proxy')
            receipt = media_sources.inspect(output)
            video = next(s for s in receipt['metadata']['streams'] if s['codec_type'] == 'video')
            if ((video['width'], video['height']) != (320, 180)
                    or abs(float(receipt['metadata']['format']['duration']) - duration) > .15):
                raise ValueError('proxy does not preserve the original media extent')
            published = directory / f'asset-{asset_id}-{generation}-{uuid.uuid4().hex}.mp4'
            with output.open('rb') as handle:
                os.fsync(handle.fileno())
            os.link(output, published)
            with db.write(conn):
                current = media_sources.get(conn, asset_id)
                if (current['proxy']['generation'] != generation
                        or current['fingerprint'] != asset['fingerprint']):
                    raise ValueError('proxy request was superseded')
                previous = current['proxy']
                if previous['path'] and Path(previous['path']).exists():
                    proxy_store.remember(conn, asset_id, previous['path'], previous['fingerprint'])
                proxy_store.remember(conn, asset_id, published, receipt['fingerprint'])
                conn.execute('UPDATE asset_proxies SET ready_generation=?,path=?,fingerprint=?,'
                    'source_fingerprint=?,metadata=?,error=NULL WHERE asset_id=?',
                    (generation, str(published), receipt['fingerprint'], asset['fingerprint'],
                     json.dumps(receipt['metadata']), asset_id))
            accepted = True
        return media_sources.get(conn, asset_id)
    except Exception as exc:
        with db.write(conn):
            conn.execute('UPDATE asset_proxies SET error=? WHERE asset_id=? AND generation=?',
                         (str(exc)[:4000], asset_id, generation))
        raise
    finally:
        if published is not None and not accepted:
            published.unlink(missing_ok=True)


def preview(conn, project_id, destination):
    with ExitStack() as pins:
        snapshot = media_sources.bind(conn, timeline.snapshot(conn, project_id), 'preview', pins=pins)
        return _preview(snapshot, destination)


def _preview(snapshot, destination):
    plan = planner.prepare(snapshot)
    destination = Path(destination).expanduser().absolute()
    destination = destination.parent.resolve() / destination.name
    if os.path.lexists(destination):
        raise ValueError('destination already exists')
    with tempfile.TemporaryDirectory(prefix='.proxy-preview-', dir=destination.parent) as temporary:
        output = encoder.render(plan, temporary)
        planner.verify_sources(plan)
        checksum = fingerprint(output)
        os.link(output, destination)
    return {'project_id': snapshot['project']['id'], 'revision': snapshot['project']['revision'],
        'output_path': str(destination), 'sha256': checksum,
        'renditions': [clip['rendition'] for clip in snapshot['clips']]}
