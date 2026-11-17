"""Versioned voice occurrences attached to an editable background timeline."""
import json
import math
from . import db, timeline
from .audio_signal import MAX_SECONDS


def replace(conn, project_id, voices, expected_revision):
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError('expected revision must be a nonnegative integer')
    if not isinstance(voices, list) or len(voices) > 16:
        raise ValueError('voices must be a list of at most 16 occurrences')
    with db.write(conn):
        revision = timeline._revision(conn, project_id)
        if revision != expected_revision:
            raise ValueError('timeline revision conflict')
        rows = []
        for position, voice in enumerate(voices):
            if not isinstance(voice, dict):
                raise ValueError('invalid voice occurrence')
            gain = voice.get('gain', 1)
            timeline._validate(conn, voice['asset_id'], voice['start_ms'], voice['end_ms'], gain)
            at_ms = voice['at_ms']
            if type(at_ms) is not int or at_ms < 0 or at_ms > MAX_SECONDS * 1000:
                raise ValueError('invalid voice position')
            duck_db = voice.get('duck_db', -12)
            if (isinstance(duck_db, bool) or not isinstance(duck_db, (int, float))
                    or not math.isfinite(duck_db) or not -48 <= duck_db <= 0):
                raise ValueError('duck attenuation must be between -48 and 0 dB')
            metadata = conn.execute('SELECT metadata FROM assets WHERE id=?',
                                    (voice['asset_id'],)).fetchone()['metadata']
            if not any(s.get('codec_type') == 'audio' for s in json.loads(metadata)['streams']):
                raise ValueError('voice source must contain audio')
            rows.append((project_id, position, voice['asset_id'], voice['start_ms'],
                         voice['end_ms'], at_ms, gain, duck_db))
        conn.execute('DELETE FROM audio_voices WHERE project_id=?', (project_id,))
        conn.executemany('INSERT INTO audio_voices '
            '(project_id,position,asset_id,start_ms,end_ms,at_ms,gain,duck_db) '
            'VALUES (?,?,?,?,?,?,?,?)', rows)
        conn.execute('UPDATE projects SET revision=revision+1 WHERE id=?', (project_id,))
    return snapshot(conn, project_id)


def snapshot(conn, project_id):
    conn.execute('SAVEPOINT audio_timeline')
    try:
        view = timeline.snapshot(conn, project_id)
        view['voices'] = [dict(row) for row in conn.execute(
            'SELECT audio_voices.*,assets.path,assets.fingerprint,assets.metadata '
            'FROM audio_voices JOIN assets ON assets.id=audio_voices.asset_id '
            'WHERE project_id=? ORDER BY position', (project_id,))]
        return view
    finally:
        conn.execute('RELEASE SAVEPOINT audio_timeline')
