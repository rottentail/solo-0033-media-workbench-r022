"""Resume verified checkpoints without allowing a superseded worker to publish."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid
from . import encoder, jobs, planner, publication, media_sources
from .segment_cache import SegmentCache, digest, verified


def run(conn, render_id, owner=None, lease_seconds=30):
    token = jobs.claim(conn, render_id, owner or uuid.uuid4().hex, lease_seconds)
    next_renewal = 0

    def tick():
        nonlocal next_renewal
        now = time.monotonic()
        if now >= next_renewal:
            jobs.renew(conn, token, lease_seconds)
            next_renewal = now + min(1, lease_seconds / 4)
        else:
            jobs.require(conn, token)

    try:
        job = jobs.get(conn, render_id)
        snapshot = media_sources.bind(conn, json.loads(job['snapshot']), tick=tick)
        planner.verify_sources(snapshot, tick)
        tick()
        # An earlier process may have linked its durable intent before dying.
        if job['artifact_path'] and verified(job['artifact_path'], job['artifact_digest'], tick):
            return publication.complete(conn, token, tick)
        if publication.owned(job['artifact_path'], job['destination']):
            raise ValueError('published artifact is corrupt; inspect the retained output before retrying')
        cache = SegmentCache(job['cache_root'])
        segments = []
        for position, clip in enumerate(snapshot['clips']):
            tick()
            path, checksum, _ = cache.ensure(clip, tick)
            jobs.checkpoint(conn, token, position, clip['cache_key'], checksum)
            segments.append(path)
        planner.verify_sources(snapshot, tick)
        directory = Path(tempfile.mkdtemp(prefix=f'.media-render-{render_id}-{token["epoch"]}-',
                                          dir=Path(job['destination']).parent))
        retained = False
        try:
            output = encoder.join(segments, directory, tick)
            checksum = digest(output, tick)
            with open(output, 'rb') as handle:
                os.fsync(handle.fileno())
            jobs.artifact(conn, token, output, checksum)
            retained = True
            return publication.complete(conn, token, tick)
        finally:
            # Recorded artifacts survive process death for publication recovery.
            if not retained:
                shutil.rmtree(directory)
    except jobs.LeaseLost:
        raise
    except Exception as exc:
        try:
            publication.fail(conn, token, exc)
        except jobs.LeaseLost:
            pass
        raise
