"""Track published proxy bytes and protect readers with process-owned locks."""
from contextlib import contextmanager
import fcntl
import hashlib
from pathlib import Path
import time
from . import db


def remember(conn, asset_id, path, checksum):
    info = Path(path).stat()
    conn.execute('INSERT OR IGNORE INTO proxy_files '
        '(path,asset_id,fingerprint,size,device,inode,created) VALUES (?,?,?,?,?,?,?)',
        (str(path), asset_id, checksum, info.st_size, info.st_dev, info.st_ino,
         time.time_ns()))


def lock_path(conn, path):
    database = conn.execute('PRAGMA database_list').fetchone()['file']
    if not database:
        raise ValueError('proxy retention requires a persistent database')
    directory = Path(database + '.proxy-locks')
    directory.mkdir(exist_ok=True)
    key = hashlib.sha256(str(path).encode()).hexdigest()
    return directory / key


@contextmanager
def pin(conn, asset_id):
    """Select and lock a receipt atomically with respect to collection."""
    handle = None
    try:
        with db.write(conn):
            row = conn.execute('SELECT * FROM asset_proxies WHERE asset_id=?',
                               (asset_id,)).fetchone()
            receipt = dict(row) if row and row['path'] else None
            if receipt:
                handle = lock_path(conn, receipt['path']).open('a+b')
                fcntl.flock(handle, fcntl.LOCK_SH)
        yield receipt
    finally:
        if handle:
            handle.close()


def collect(conn, directory, max_bytes):
    """Reclaim oldest retired files, skipping every current or pinned receipt."""
    directory = Path(directory).expanduser().resolve(strict=True)
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError('proxy budget must be a nonnegative integer')
    removed, busy, changed = [], [], []
    with db.write(conn):
        rows = [dict(row) for row in conn.execute(
            'SELECT * FROM proxy_files ORDER BY created,path')
            if Path(row['path']).parent == directory]
        current = {row['path'] for row in conn.execute(
            'SELECT path FROM asset_proxies WHERE path IS NOT NULL')}
        existing = []
        for row in rows:
            try:
                info = Path(row['path']).lstat()
            except FileNotFoundError:
                conn.execute('DELETE FROM proxy_files WHERE path=?', (row['path'],))
                continue
            row['actual_size'] = info.st_size
            existing.append(row)
        remaining = sum(row['actual_size'] for row in existing)
        for row in existing:
            if remaining <= max_bytes:
                break
            path = row['path']
            if path in current:
                continue
            with lock_path(conn, path).open('a+b') as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    busy.append(path)
                    continue
                try:
                    info = Path(path).lstat()
                except FileNotFoundError:
                    conn.execute('DELETE FROM proxy_files WHERE path=?', (path,))
                    remaining -= row['actual_size']
                    continue
                if (info.st_dev, info.st_ino, info.st_size) != (
                        row['device'], row['inode'], row['size']):
                    changed.append(path)
                    continue
                # Only service-owned immutable names are candidates. A replaced
                # name belongs to its new writer, even if it occupies our store.
                Path(path).unlink()
                conn.execute('DELETE FROM proxy_files WHERE path=?', (path,))
                remaining -= row['actual_size']
                removed.append(path)
    return {'removed': removed, 'busy': busy, 'changed': changed,
            'remaining_bytes': remaining, 'over_budget': remaining > max_bytes}
