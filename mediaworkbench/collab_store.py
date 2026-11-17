"""Commit collaborative batches and replay receipts in one short database transaction."""
import hashlib
import json
import uuid
import zlib
from . import db, timeline, collab_merge


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def save_tree(conn, items):
    priorities = [hashlib.blake2b((item['uid'] + ':' + str(item['start_ms'])).encode(), digest_size=16).digest() for item in items]
    def build(left, right):
        if left == right:
            return ''
        middle = min(range(left, right), key=priorities.__getitem__)
        value = [items[middle], build(left, middle), build(middle + 1, right)]
        raw = encoded(value).encode()
        key = hashlib.blake2b(raw, digest_size=16).digest()
        conn.execute('INSERT OR IGNORE INTO edit_nodes VALUES (?,?)', (key, zlib.compress(raw)))
        return key.hex()
    return build(0, len(items))


def load_tree(conn, root):
    if not root:
        return []
    row = conn.execute('SELECT payload FROM edit_nodes WHERE key=?', (bytes.fromhex(root),)).fetchone()
    if row is None:
        raise ValueError('edit history node is missing')
    item, left, right = json.loads(zlib.decompress(row['payload']))
    return load_tree(conn, left) + [item] + load_tree(conn, right)


def version(conn, project_id, revision):
    row = conn.execute('SELECT payload FROM edit_versions WHERE project_id=? AND revision=?', (project_id, revision)).fetchone()
    if row is None:
        raise ValueError('viewed revision is unavailable; reopen the timeline')
    return load_tree(conn, row['payload'])


def seed(conn, project_id):
    snapshot = timeline.snapshot(conn, project_id)
    revision = snapshot['project']['revision']
    row = conn.execute('SELECT payload FROM edit_versions WHERE project_id=? AND revision=?',
                       (project_id, revision)).fetchone()
    if row:
        return revision, load_tree(conn, row['payload'])
    spans = [{'uid': uuid.uuid4().hex, 'stamp': 'initial', 'born': revision, 'asset_id': c['asset_id'],
              'start_ms': c['start_ms'], 'end_ms': c['end_ms'], 'gain': c['gain']}
             for c in snapshot['clips']]
    head = conn.execute('SELECT * FROM edit_heads WHERE project_id=?', (project_id,)).fetchone()
    ids = [c['id'] for c in snapshot['clips']]
    if head:
        old_ids = json.loads(head['clip_ids'])
        if ids[:len(old_ids)] == old_ids:
            old = version(conn, project_id, head['revision'])
            spans = old + spans[len(old):]
    conn.execute('INSERT OR REPLACE INTO edit_heads VALUES (?,?,?)', (project_id, revision, encoded(ids)))
    conn.execute('INSERT INTO edit_versions VALUES (?,?,?)', (project_id, revision, save_tree(conn, spans)))
    return revision, spans


def view(conn, project_id):
    with db.write(conn):
        revision, spans = seed(conn, project_id)
    return {'project_id': project_id, 'revision': revision, 'spans': spans}


def commit(conn, project_id, actor, request_id, base_revision=None, actions=None, undo_request=None):
    if not all(isinstance(x, str) and 0 < len(x) <= 128 for x in (actor, request_id)):
        raise ValueError('actor and request identity are required')
    if undo_request is None:
        if type(base_revision) is not int or not isinstance(actions, list) or not 1 <= len(actions) <= 32:
            raise ValueError('a viewed revision and one to thirty-two edits are required')
    elif actions is not None or base_revision is not None or not isinstance(undo_request, str):
        raise ValueError('undo must identify only the original request')
    body = {'base_revision': base_revision, 'actions': actions, 'undo_request': undo_request}
    checksum = hashlib.sha256(encoded(body).encode()).hexdigest()
    with db.write(conn):
        previous = conn.execute('SELECT * FROM edit_receipts WHERE project_id=? AND actor=? AND request_id=?',
                                 (project_id, actor, request_id)).fetchone()
        if previous:
            if previous['body_sha256'] != checksum:
                raise ValueError('request identity was reused for a different edit')
            response = json.loads(previous['response'])
            response['spans'] = version(conn, project_id, response['revision'])
            return response
        revision, current = seed(conn, project_id)
        stamp = str(revision + 1)
        if undo_request is not None:
            original = conn.execute('SELECT * FROM edit_receipts WHERE project_id=? AND actor=? AND request_id=?',
                                    (project_id, actor, undo_request)).fetchone()
            if original is None or original['undone_by'] is not None:
                raise ValueError('edit is absent or was already undone')
            result, inverses = collab_merge.undo(current, json.loads(original['inverse']), stamp)
        else:
            base = version(conn, project_id, base_revision)
            result, inverses = collab_merge.apply(base, current, actions, stamp, base_revision)
        if len(result) > 512:
            raise ValueError('timeline exceeds 512 occurrences')
        for clip in result:
            timeline._validate(conn, clip['asset_id'], clip['start_ms'], clip['end_ms'], clip['gain'])
        conn.execute('DELETE FROM clips WHERE project_id=?', (project_id,))
        conn.executemany('INSERT INTO clips(project_id,asset_id,start_ms,end_ms,position,gain) VALUES (?,?,?,?,?,?)',
            [(project_id, c['asset_id'], c['start_ms'], c['end_ms'], i, c['gain']) for i, c in enumerate(result)])
        conn.execute('UPDATE projects SET revision=revision+1 WHERE id=?', (project_id,))
        conn.execute('INSERT INTO edit_versions VALUES (?,?,?)', (project_id, revision + 1, save_tree(conn, result)))
        ids = [row['id'] for row in conn.execute('SELECT id FROM clips WHERE project_id=? ORDER BY position', (project_id,))]
        conn.execute('INSERT OR REPLACE INTO edit_heads VALUES (?,?,?)', (project_id, revision + 1, encoded(ids)))
        response = {'project_id': project_id, 'revision': revision + 1,
                    'actor': actor, 'request_id': request_id}
        conn.execute('INSERT INTO edit_receipts(project_id,actor,request_id,body_sha256,response,inverse) VALUES (?,?,?,?,?,?)',
            (project_id, actor, request_id, checksum, encoded(response), encoded(inverses)))
        if undo_request is not None:
            conn.execute('UPDATE edit_receipts SET undone_by=? WHERE project_id=? AND actor=? AND request_id=?',
                         (request_id, project_id, actor, undo_request))
    response['spans'] = result
    return response
