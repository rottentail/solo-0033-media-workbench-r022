"""Atomic ordered edits, optimistic revisions and short consistent read views."""
import json
import math
from . import db


def project(conn, name):
    if not isinstance(name, str) or not name.strip():
        raise ValueError('name is required')
    with db.write(conn):
        cursor = conn.execute('INSERT INTO projects(name) VALUES (?)', (name.strip(),))
    return {'id': cursor.lastrowid, 'revision': 0}


def _validate(conn, asset_id, start_ms, end_ms, gain):
    if type(start_ms) is not int or type(end_ms) is not int or start_ms < 0 or end_ms <= start_ms:
        raise ValueError('invalid clip boundaries')
    if isinstance(gain, bool) or not isinstance(gain, (float, int)) or not math.isfinite(gain) or not 0 <= gain <= 2:
        raise ValueError('gain must be between 0 and 2')
    asset = conn.execute('SELECT metadata FROM assets WHERE id=?', (asset_id,)).fetchone()
    if asset is None:
        raise ValueError('asset not found')
    duration = float(json.loads(asset['metadata'])['format']['duration']) * 1000
    if end_ms > round(duration):
        raise ValueError('clip exceeds source duration')


def _revision(conn, project_id):
    project = conn.execute('SELECT revision FROM projects WHERE id=?', (project_id,)).fetchone()
    if project is None:
        raise ValueError('project not found')
    return project['revision']


def add_clip(conn, project_id, asset_id, start_ms, end_ms, gain=1.0):
    with db.write(conn):
        revision = _revision(conn, project_id)
        _validate(conn, asset_id, start_ms, end_ms, gain)
        position = conn.execute('SELECT count(*) FROM clips WHERE project_id=?', (project_id,)).fetchone()[0]
        cursor = conn.execute('INSERT INTO clips(project_id,asset_id,start_ms,end_ms,position,gain) VALUES (?,?,?,?,?,?)',
                              (project_id, asset_id, start_ms, end_ms, position, gain))
        conn.execute('UPDATE projects SET revision=revision+1 WHERE id=?', (project_id,))
    return {'id': cursor.lastrowid, 'revision': revision + 1}


def replace(conn, project_id, clips, expected_revision):
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError('expected_revision must be a nonnegative integer')
    if not isinstance(clips, list) or len(clips) > 512:
        raise ValueError('clips must be a list of at most 512 occurrences')
    with db.write(conn):
        revision = _revision(conn, project_id)
        if revision != expected_revision:
            raise ValueError('timeline revision conflict')
        for clip in clips:
            if not isinstance(clip, dict):
                raise ValueError('invalid clip')
            _validate(conn, clip['asset_id'], clip['start_ms'], clip['end_ms'], clip.get('gain', 1))
        conn.execute('DELETE FROM edit_heads WHERE project_id=?', (project_id,))
        conn.execute('DELETE FROM clips WHERE project_id=?', (project_id,))
        conn.executemany(
            'INSERT INTO clips(project_id,asset_id,start_ms,end_ms,position,gain) VALUES (?,?,?,?,?,?)',
            [(project_id, c['asset_id'], c['start_ms'], c['end_ms'], i, c.get('gain', 1))
             for i, c in enumerate(clips)],
        )
        conn.execute('UPDATE projects SET revision=revision+1 WHERE id=?', (project_id,))
    return snapshot(conn, project_id)


def snapshot(conn, project_id):
    conn.execute('SAVEPOINT timeline_read')
    try:
        project = conn.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
        if not project:
            raise ValueError('project not found')
        clips = [dict(row) for row in conn.execute(
            'SELECT clips.*,assets.path,assets.fingerprint,assets.metadata '
            'FROM clips JOIN assets ON assets.id=clips.asset_id '
            'WHERE project_id=? ORDER BY position,id', (project_id,),
        )]
        return {'project': dict(project), 'clips': clips}
    finally:
        conn.execute('RELEASE SAVEPOINT timeline_read')
