"""Durable render claims and fenced state changes."""
import json
import math
import time
from . import planner, timeline, media_sources
from .db import write


class LeaseLost(ValueError):
    pass


def get(conn, render_id):
    row = conn.execute(
        'SELECT renders.*,render_jobs.destination,render_jobs.cache_root,'
        'render_jobs.epoch,render_jobs.owner,render_jobs.lease_until,'
        'render_jobs.artifact_path,render_jobs.artifact_digest '
        'FROM renders JOIN render_jobs ON render_jobs.render_id=renders.id '
        'WHERE renders.id=?', (render_id,),
    ).fetchone()
    if row is None:
        raise ValueError('render job not found')
    result = dict(row)
    result['segments'] = [dict(row) for row in conn.execute(
        'SELECT position,cache_key,status,digest FROM render_segments '
        'WHERE render_id=? ORDER BY position', (render_id,),
    )]
    return result


def enqueue(conn, project_id, destination, cache_root):
    frozen = planner.prepare(media_sources.bind(conn, timeline.snapshot(conn, project_id)))
    with write(conn):
        cursor = conn.execute(
            'INSERT INTO renders(project_id,revision,status,snapshot) VALUES (?,?,?,?)',
            (project_id, frozen['project']['revision'], 'queued', json.dumps(frozen)),
        )
        render_id = cursor.lastrowid
        conn.execute(
            'INSERT INTO render_jobs(render_id,destination,cache_root) VALUES (?,?,?)',
            (render_id, str(destination), str(cache_root)),
        )
        conn.executemany(
            'INSERT INTO render_segments(render_id,position,cache_key,status) '
            'VALUES (?,?,?,?)',
            [(render_id, i, clip['cache_key'], 'pending')
             for i, clip in enumerate(frozen['clips'])],
        )
    return get(conn, render_id)


def _duration(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('invalid lease duration')
    if not math.isfinite(value) or not .2 <= value <= 3600:
        raise ValueError('lease duration must be between 0.2 and 3600 seconds')
    return value


def claim(conn, render_id, owner, seconds=30, now=None):
    seconds = _duration(seconds)
    if not isinstance(owner, str) or not owner.strip():
        raise ValueError('worker identity is required')
    with write(conn):
        now = time.time() if now is None else now
        job = get(conn, render_id)
        if job['status'] != 'queued' and not (
            job['status'] == 'running' and job['lease_until'] <= now
        ):
            raise ValueError('job is terminal or already claimed')
        epoch = job['epoch'] + 1
        conn.execute(
            'UPDATE render_jobs SET epoch=?,owner=?,lease_until=? WHERE render_id=?',
            (epoch, owner, now + seconds, render_id),
        )
        conn.execute('UPDATE renders SET status=?,error=NULL WHERE id=?', ('running', render_id))
    return {'render_id': render_id, 'owner': owner, 'epoch': epoch}


def require(conn, token, now=None):
    now = time.time() if now is None else now
    job = get(conn, token['render_id'])
    if (job['status'] != 'running' or job['epoch'] != token['epoch']
            or job['owner'] != token['owner'] or job['lease_until'] <= now):
        raise LeaseLost('worker claim expired, was cancelled or was superseded')
    return job


def renew(conn, token, seconds=30, now=None):
    seconds = _duration(seconds)
    with write(conn):
        now = time.time() if now is None else now
        require(conn, token, now)
        conn.execute('UPDATE render_jobs SET lease_until=? WHERE render_id=?',
                     (now + seconds, token['render_id']))


def checkpoint(conn, token, position, key, digest):
    with write(conn):
        require(conn, token)
        cursor = conn.execute(
            'UPDATE render_segments SET status=?,digest=? '
            'WHERE render_id=? AND position=? AND cache_key=?',
            ('ready', digest, token['render_id'], position, key),
        )
        if cursor.rowcount != 1:
            raise ValueError('checkpoint does not match the frozen plan')


def artifact(conn, token, path, digest):
    with write(conn):
        require(conn, token)
        conn.execute(
            'UPDATE render_jobs SET artifact_path=?,artifact_digest=? WHERE render_id=?',
            (str(path), digest, token['render_id']),
        )


def finish(conn, token, error=None):
    """Caller holds the same write transaction used to publish the file."""
    if not conn.in_transaction:
        raise ValueError('publication transaction is required')
    job = require(conn, token)
    status = 'failed' if error is not None else 'completed'
    conn.execute('UPDATE renders SET status=?,output_path=?,error=? WHERE id=?',
                 (status, None if error is not None else job['destination'],
                  str(error)[:4000] if error is not None else None, token['render_id']))
    conn.execute('UPDATE render_jobs SET owner=NULL,lease_until=0 WHERE render_id=?',
                 (token['render_id'],))


def retry(conn, render_id):
    with write(conn):
        job = get(conn, render_id)
        if job['status'] != 'failed':
            raise ValueError('only failed renders can be retried')
        conn.execute('UPDATE renders SET status=?,error=NULL,output_path=NULL WHERE id=?',
                     ('queued', render_id))
        conn.execute('UPDATE render_jobs SET epoch=epoch+1,owner=NULL,lease_until=0 '
                     'WHERE render_id=?', (render_id,))
    return get(conn, render_id)
