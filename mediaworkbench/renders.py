"""Synchronous exports and durable, explicitly driven render jobs."""
import os
from pathlib import Path
from . import jobs, publication, worker


def queue(conn, project_id, destination, cache_root=None):
    destination = Path(destination).expanduser().absolute()
    # Keep the final path component intact so dangling symlinks are protected.
    destination = destination.parent.resolve() / destination.name
    if os.path.lexists(destination):
        raise ValueError('destination already exists')
    if not destination.parent.is_dir():
        raise ValueError('destination directory does not exist')
    database = next((row[2] for row in conn.execute('PRAGMA database_list') if row[1] == 'main'), '')
    parent = Path(database).parent if database else destination.parent
    root = Path(cache_root).expanduser().resolve() if cache_root else parent / '.media-workbench-cache'
    return jobs.enqueue(conn, project_id, destination, root)


def create(conn, project_id, destination):
    job = queue(conn, project_id, destination)
    result = worker.run(conn, job['id'])
    return {key: result[key] for key in ('id', 'status', 'output_path', 'revision')}


def cancel(conn, render_id):
    return publication.cancel(conn, render_id)


def get(conn, render_id):
    row = conn.execute('SELECT * FROM renders WHERE id=?', (render_id,)).fetchone()
    if not row:
        raise ValueError('render not found')
    result = dict(row)
    if conn.execute('SELECT 1 FROM render_jobs WHERE render_id=?', (render_id,)).fetchone():
        result = jobs.get(conn, render_id)
    return result
