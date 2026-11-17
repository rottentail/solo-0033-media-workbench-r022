"""Versioned source transcripts and their occurrence-local programme projection."""
import json
import unicodedata
from . import db, timeline


def get(conn, asset_id):
    asset = conn.execute('SELECT metadata,fingerprint FROM assets WHERE id=?', (asset_id,)).fetchone()
    if asset is None:
        raise ValueError('asset not found')
    row = conn.execute('SELECT * FROM caption_transcripts WHERE asset_id=?', (asset_id,)).fetchone()
    return {'asset_id': asset_id, 'revision': row['revision'] if row else 0,
            'fingerprint': asset['fingerprint'], 'words': json.loads(row['words']) if row else []}


def replace(conn, asset_id, words, expected_revision):
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError('transcript revision must be a nonnegative integer')
    if not isinstance(words, list) or len(words) > 128:
        raise ValueError('a source transcript supports at most 128 word atoms')
    validated = []
    previous = 0
    for word in words:
        start, end, text = word['start_ms'], word['end_ms'], word['text']
        if (type(start) is not int or type(end) is not int or start < previous or end <= start
                or not isinstance(text, str) or not text or len(text) > 80
                or any(ch.isspace() or unicodedata.category(ch)[0] == 'C' for ch in text)):
            raise ValueError('word atoms must be ordered nonoverlapping intervals and printable text without spaces')
        validated.append({'start_ms': start, 'end_ms': end, 'text': unicodedata.normalize('NFC', text)})
        previous = end
    with db.write(conn):
        current = get(conn, asset_id)
        metadata = json.loads(conn.execute('SELECT metadata FROM assets WHERE id=?', (asset_id,)).fetchone()[0])
        if current['revision'] != expected_revision:
            raise ValueError('transcript revision conflict')
        if previous > round(float(metadata['format']['duration'])*1000):
            raise ValueError('transcript extends beyond the original')
        conn.execute('INSERT INTO caption_transcripts(asset_id,revision,words) VALUES (?,?,?) '
                     'ON CONFLICT(asset_id) DO UPDATE SET revision=excluded.revision,words=excluded.words',
                     (asset_id, expected_revision+1, json.dumps(validated, ensure_ascii=False)))
        result = get(conn, asset_id)
    return result


def capture(conn, project_id):
    conn.execute('SAVEPOINT caption_read')
    try:
        snapshot = timeline.snapshot(conn, project_id)
        transcripts = {clip['asset_id']: get(conn, clip['asset_id']) for clip in snapshot['clips']}
        return snapshot, transcripts
    finally:
        conn.execute('RELEASE SAVEPOINT caption_read')


def project(snapshot, transcripts):
    tokens = []
    offset = 0
    for occurrence, clip in enumerate(snapshot['clips']):
        start, end = clip['start_ms'], clip['end_ms']
        transcript = transcripts[clip['asset_id']]
        for source_index, word in enumerate(transcript['words']):
            # A cut through a word omits that incomplete atom rather than
            # presenting a complete printed word over an incomplete utterance.
            if word['start_ms'] < start or word['end_ms'] > end:
                continue
            tokens.append({'text': word['text'], 'start_ms': offset+word['start_ms']-start,
                'end_ms': offset+word['end_ms']-start, 'clip_end_ms': offset+end-start,
                'occurrence': occurrence, 'source_index': source_index, 'asset_id': clip['asset_id'],
                'transcript_revision': transcript['revision']})
        offset += end-start
    if not snapshot['clips'] or offset > 600000 or len(tokens) > 128:
        raise ValueError('caption programmes need clips, at most ten minutes and 128 projected words')
    return tokens, offset
