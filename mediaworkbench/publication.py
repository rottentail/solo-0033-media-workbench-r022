"""Recoverable no-overwrite publication under the current database claim."""
import os
from pathlib import Path
from . import jobs
from .segment_cache import verified


def owned(source, destination):
    if not source:
        return False
    try:
        return os.path.samestat(Path(source).stat(), Path(destination).lstat())
    except FileNotFoundError:
        return False


def complete(conn, token, tick=None):
    candidate = jobs.require(conn, token)
    source = candidate['artifact_path']
    if not source:
        raise ValueError('assembled artifact is missing or corrupt')
    before = Path(source).stat()
    if not verified(source, candidate['artifact_digest'], tick):
        raise ValueError('assembled artifact is missing or corrupt')
    with jobs.write(conn):
        job = jobs.require(conn, token)
        after = Path(source).stat()
        if (job['artifact_path'] != source or job['artifact_digest'] != candidate['artifact_digest']
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            raise ValueError('assembled artifact changed during publication')
        if not owned(source, job['destination']):
            os.link(source, job['destination'])
        jobs.finish(conn, token)
    return jobs.get(conn, token['render_id'])


def fail(conn, token, error):
    # Never unlink a public name: an external writer may replace it at any time.
    # The durable intent allows a valid published artifact to be adopted on retry.
    with jobs.write(conn):
        jobs.finish(conn, token, error)


def cancel(conn, render_id):
    with jobs.write(conn):
        job = jobs.get(conn, render_id)
        if job['status'] == 'completed':
            raise ValueError('completed render cannot be cancelled')
        if job['status'] in ('queued', 'running'):
            conn.execute('UPDATE renders SET status=?,error=? WHERE id=?',
                         ('cancelled', 'cancelled by operator', render_id))
            conn.execute('UPDATE render_jobs SET epoch=epoch+1,owner=NULL,lease_until=0 '
                         'WHERE render_id=?', (render_id,))
    return jobs.get(conn, render_id)
