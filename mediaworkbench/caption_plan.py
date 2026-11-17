"""Frozen caption previews and portable WebVTT delivery from the same cues."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from . import caption_layout, caption_words, db


def create(conn, project_id, width=32, max_cues=32, minimum_ms=400, maximum_ms=5000, hold_ms=500):
    snapshot, transcripts = caption_words.capture(conn, project_id)
    tokens, duration = caption_words.project(snapshot, transcripts)
    settings = dict(width=width, max_cues=max_cues, minimum_ms=minimum_ms,
                    maximum_ms=maximum_ms, hold_ms=hold_ms)
    layout = caption_layout.solve(tokens, duration, **settings)
    plan = dict(version=1, project=snapshot['project'], clips=snapshot['clips'],
                transcripts=transcripts, tokens=tokens, duration_ms=duration, settings=settings, **layout)
    payload = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    with db.write(conn):
        cursor = conn.execute('INSERT INTO caption_plans(project_id,payload,payload_sha256) VALUES (?,?,?)',
                              (project_id, payload, hashlib.sha256(payload.encode()).hexdigest()))
    return get(conn, cursor.lastrowid)


def get(conn, plan_id):
    row = conn.execute('SELECT * FROM caption_plans WHERE id=?', (plan_id,)).fetchone()
    if row is None or hashlib.sha256(row['payload'].encode()).hexdigest() != row['payload_sha256']:
        raise ValueError('caption plan is missing or damaged')
    return dict(json.loads(row['payload']), id=plan_id)


def preview(conn, plan_id, at_ms):
    plan = get(conn, plan_id)
    if type(at_ms) is not int or not 0 <= at_ms <= plan['duration_ms']:
        raise ValueError('preview time lies outside the programme')
    return {'plan_id': plan_id, 'revision': plan['project']['revision'], 'at_ms': at_ms,
            'cues': [cue for cue in plan['cues'] if cue['start_ms'] <= at_ms < cue['end_ms']]}


def timestamp(value):
    seconds, ms = divmod(value, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}.{ms:03d}'


def vtt(plan):
    rows = ['WEBVTT', '']
    for number, cue in enumerate(plan['cues'], 1):
        # VTT markup must not turn a literal transcription into tags or entities.
        text = [line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                for line in cue['lines']]
        rows.extend([str(number), timestamp(cue['start_ms'])+' --> '+timestamp(cue['end_ms']), *text, ''])
    return '\n'.join(rows)+'\n'


def export(conn, plan_id, destination):
    plan = get(conn, plan_id)
    target = Path(destination).expanduser().absolute()
    target = target.parent.resolve()/target.name
    if os.path.lexists(target) or not target.parent.is_dir():
        raise ValueError('caption destination exists or directory is missing')
    body = vtt(plan).encode('utf-8')
    with tempfile.TemporaryDirectory(prefix='.caption-', dir=target.parent) as private:
        output = Path(private)/'captions.vtt'
        with output.open('wb') as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(output, target)
    return {'plan_id': plan_id, 'revision': plan['project']['revision'], 'path': str(target),
            'sha256': hashlib.sha256(body).hexdigest(), 'cue_count': len(plan['cues'])}
