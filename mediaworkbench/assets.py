"""Probe and register immutable media inputs."""
import hashlib
import json
from pathlib import Path
from .tools import run

def fingerprint(path, tick=None):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
            if tick is not None:
                tick()
    return digest.hexdigest()

def register(conn, path):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError('asset must be a regular file')
    info = json.loads(run('ffprobe', ['-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)]))
    duration = float(info.get('format', {}).get('duration', 0))
    if duration <= 0 or not any(s.get('codec_type') in ('audio', 'video') for s in info.get('streams', [])):
        raise ValueError('asset requires a finite duration and an audio or video stream')
    digest = fingerprint(path)
    with conn:
        cur = conn.execute('INSERT INTO assets(path,fingerprint,metadata) VALUES (?,?,?)', (str(path), digest, json.dumps(info)))
    return {'id': cur.lastrowid, 'duration_ms': round(duration * 1000), 'fingerprint': digest}
