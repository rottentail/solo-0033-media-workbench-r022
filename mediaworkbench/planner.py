"""Freeze occurrence-specific media decisions and reproducible cache identities."""
import copy
import hashlib
import json
from .assets import fingerprint

PROFILE = {'version': 1, 'width': 640, 'height': 360, 'fps': 25,
           'sample_rate': 48000, 'channels': 2, 'video': 'libx264', 'audio': 'aac'}


def cache_key(clip):
    contract = {'profile': PROFILE, 'source': clip['fingerprint'],
                'start_ms': clip['start_ms'], 'end_ms': clip['end_ms'],
                'gain': float(clip['gain']), 'has_audio': clip['has_audio']}
    payload = json.dumps(contract, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode()).hexdigest()


def prepare(snapshot):
    frozen = copy.deepcopy(snapshot)
    if not frozen['clips']:
        raise ValueError('timeline is empty')
    checked = set()
    for clip in frozen['clips']:
        identity = (clip['path'], clip['fingerprint'])
        if identity not in checked:
            if fingerprint(clip['path']) != clip['fingerprint']:
                raise ValueError('source changed since intake')
            checked.add(identity)
        streams = json.loads(clip['metadata'])['streams']
        if not any(stream.get('codec_type') == 'video' for stream in streams):
            raise ValueError('each clip must contain video')
        clip['has_audio'] = any(stream.get('codec_type') == 'audio' for stream in streams)
        clip['cache_key'] = cache_key(clip)
    frozen['profile'] = dict(PROFILE)
    return frozen


def verify_sources(snapshot, tick=None):
    checked = set()
    for clip in snapshot['clips']:
        identity = (clip['path'], clip['fingerprint'])
        if identity not in checked:
            if fingerprint(clip['path'], tick) != clip['fingerprint']:
                raise ValueError('source changed since intake')
            checked.add(identity)
