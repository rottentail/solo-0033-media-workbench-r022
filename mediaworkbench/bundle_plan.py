"""Freeze project occurrences and protect exact proxy versions for offline packing."""
import copy
import fcntl
import json
from pathlib import Path
from . import db, proxy_store, timeline


def capture(conn, requests, stack):
    if not isinstance(requests, list) or not 1 <= len(requests) <= 16:
        raise ValueError('one to sixteen weighted projects are required')
    seen = set()
    for item in requests:
        if (not isinstance(item, dict) or type(item.get('project_id')) is not int
                or item['project_id'] <= 0 or item['project_id'] in seen
                or type(item.get('priority')) is not int or not 1 <= item['priority'] <= 1000):
            raise ValueError('unique projects and priorities from one to one thousand required')
        seen.add(item['project_id'])
    projects, media, locks, unavailable = [], {}, {}, []
    # Collection and publication use this same short writer boundary. No media
    # reads, optimization, or ZIP writes happen while the database is locked.
    with db.write(conn):
        for request in requests:
            snapshot = timeline.snapshot(conn, request['project_id'])
            clips, required = [], set()
            available = bool(snapshot['clips'])
            for occurrence in snapshot['clips']:
                row = conn.execute('SELECT * FROM asset_proxies WHERE asset_id=?',
                                   (occurrence['asset_id'],)).fetchone()
                if not row or not row['path'] or row['source_fingerprint'] != occurrence['fingerprint']:
                    available = False
                    break
                receipt = dict(row)
                path = receipt['path']
                if path not in locks:
                    handle = stack.enter_context(proxy_store.lock_path(conn, path).open('a+b'))
                    fcntl.flock(handle, fcntl.LOCK_SH)
                    locks[path] = handle
                try:
                    size = Path(path).stat().st_size
                except OSError:
                    available = False
                    break
                key = receipt['fingerprint']
                if size <= 0 or key in media and size != media[key]['size']:
                    raise ValueError('proxy receipt has an inconsistent byte extent')
                media[key] = dict(receipt, size=size)
                clip = copy.deepcopy(occurrence)
                clip.update(path='media/'+key+'.mp4', fingerprint=key,
                            source_fingerprint=occurrence['fingerprint'],
                            metadata=receipt['metadata'], rendition='proxy')
                clips.append(clip)
                required.add(key)
            if not available:
                unavailable.append(request['project_id'])
                continue
            projects.append({'id': request['project_id'], 'priority': request['priority'],
                'required': sorted(required), 'snapshot': {'project': snapshot['project'], 'clips': clips}})
    return {'projects': projects, 'media': media, 'locks': locks, 'unavailable': sorted(unavailable)}


def manifest(plan, selection):
    selected = set(selection['project_ids'])
    return {'version': 1, 'value': selection['value'], 'media_bytes': selection['media_bytes'],
        'unavailable': plan['unavailable'],
        'projects': [copy.deepcopy(p) for p in sorted(plan['projects'], key=lambda p: p['id'])
                     if p['id'] in selected],
        'media': {key: {'path': 'media/'+key+'.mp4', 'size': plan['media'][key]['size'],
                       'sha256': key} for key in selection['media']}}


def volume_manifest(plan, allocation, volume):
    selection = allocation['volumes'][volume]
    description = manifest(plan, selection)
    description['volume'] = volume
    description['volume_count'] = len(allocation['volumes'])
    description['set_value'] = allocation['value']
    return description
