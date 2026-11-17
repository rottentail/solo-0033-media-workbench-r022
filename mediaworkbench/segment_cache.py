"""Verified immutable media segments, serialized across worker processes."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from . import encoder
from .assets import fingerprint
from .tools import run


def digest(path, tick=None):
    result = hashlib.sha256()
    with open(path, 'rb') as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            result.update(chunk)
            if tick is not None:
                tick()
    return result.hexdigest()


def verified(path, expected, tick=None):
    try:
        return Path(path).is_file() and digest(path, tick) == expected
    except FileNotFoundError:
        return False


class SegmentCache:
    def __init__(self, root):
        self.root = Path(root).resolve()
        (self.root / 'segments').mkdir(parents=True, exist_ok=True)
        (self.root / 'locks').mkdir(exist_ok=True)

    def paths(self, key):
        if not isinstance(key, str) or re.fullmatch('[0-9a-f]{64}', key) is None:
            raise ValueError('invalid segment identity')
        return (self.root / 'segments' / (key + '.mp4'),
                self.root / 'segments' / (key + '.json'))

    def _existing(self, key, tick):
        media, manifest = self.paths(key)
        try:
            record = json.loads(manifest.read_text(encoding='utf-8'))
            if (record['key'] == key and record['size'] == media.stat().st_size
                    and verified(media, record['digest'], tick)):
                return record
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError):
            pass
        return None

    def ensure(self, clip, tick):
        key = clip['cache_key']
        media, manifest = self.paths(key)
        with open(self.root / 'locks' / (key + '.lock'), 'a+b') as lock:
            while True:
                tick()
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(.05)
            # Every consumer revalidates bytes, including a resumed checkpoint.
            record = self._existing(key, tick)
            if record is not None:
                return media, record['digest'], True
            with tempfile.TemporaryDirectory(prefix='encode-', dir=self.root) as tmp:
                output = Path(tmp) / 'segment.mp4'
                encoder.segment(clip, output, tick)
                if fingerprint(clip['path'], tick) != clip['fingerprint']:
                    raise ValueError('source changed during encoding')
                info = json.loads(run('ffprobe', ['-v', 'error', '-show_streams',
                    '-show_format', '-of', 'json', str(output)], tick=tick))
                video = next((s for s in info['streams'] if s['codec_type'] == 'video'), {})
                audio = next((s for s in info['streams'] if s['codec_type'] == 'audio'), {})
                duration = (clip['end_ms'] - clip['start_ms']) / 1000
                if ((video.get('width'), video.get('height')) != (640, 360)
                        or video.get('r_frame_rate') != '25/1'
                        or audio.get('sample_rate') != '48000'
                        or audio.get('channels') != 2
                        or abs(float(info['format']['duration']) - duration) > .15):
                    raise ValueError('encoded segment does not match the media profile')
                record = {'key': key, 'digest': digest(output, tick),
                          'size': output.stat().st_size}
                receipt = Path(tmp) / 'segment.json'
                receipt.write_text(json.dumps(record, sort_keys=True), encoding='utf-8')
                tick()
                # Readers also hold the key lock, so an interrupted pair is a miss.
                with open(output, 'rb') as handle:
                    os.fsync(handle.fileno())
                os.replace(output, media)
                with open(receipt, 'rb') as handle:
                    os.fsync(handle.fileno())
                os.replace(receipt, manifest)
            return media, record['digest'], False
