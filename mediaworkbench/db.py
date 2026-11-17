"""Persistent state for the media processing service."""
import sqlite3
from contextlib import contextmanager

SCHEMA = '''
CREATE TABLE IF NOT EXISTS lens_profiles (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS lens_plans (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rolling_plans (
    id INTEGER PRIMARY KEY, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sphere_plans (
    id INTEGER PRIMARY KEY, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stitch_profiles (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stitch_plans (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS echo_profiles (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS echo_plans (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS repair_marks (asset_id INTEGER PRIMARY KEY REFERENCES assets(id), revision INTEGER NOT NULL, intervals TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS repair_plans (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS resample_plans (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stretch_plans (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS color_plans (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS master_plans (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS music_catalogues (project_id INTEGER PRIMARY KEY REFERENCES projects(id), revision INTEGER NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS music_plans (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS room_plans (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS noise_profiles (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS denoise_plans (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS caption_transcripts (asset_id INTEGER PRIMARY KEY REFERENCES assets(id), revision INTEGER NOT NULL, words TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS caption_plans (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS spatial_plans (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS multicam_plans (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS edit_heads (project_id INTEGER PRIMARY KEY, revision INTEGER NOT NULL, clip_ids TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS edit_nodes (key BLOB PRIMARY KEY, payload BLOB NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS edit_versions (project_id INTEGER NOT NULL REFERENCES projects(id), revision INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(project_id,revision));
CREATE TABLE IF NOT EXISTS edit_receipts (project_id INTEGER NOT NULL REFERENCES projects(id), actor TEXT NOT NULL, request_id TEXT NOT NULL, body_sha256 TEXT NOT NULL, response TEXT NOT NULL, inverse TEXT NOT NULL, undone_by TEXT, PRIMARY KEY(project_id,actor,request_id));
CREATE TABLE IF NOT EXISTS assets (id INTEGER PRIMARY KEY, path TEXT NOT NULL, fingerprint TEXT NOT NULL, metadata TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS clips (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), asset_id INTEGER NOT NULL REFERENCES assets(id), start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, position INTEGER NOT NULL, gain REAL NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS renders (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), revision INTEGER NOT NULL, status TEXT NOT NULL, snapshot TEXT NOT NULL, output_path TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS render_jobs (
    render_id INTEGER PRIMARY KEY REFERENCES renders(id),
    destination TEXT NOT NULL, cache_root TEXT NOT NULL,
    epoch INTEGER NOT NULL DEFAULT 0, owner TEXT,
    lease_until REAL NOT NULL DEFAULT 0,
    artifact_path TEXT, artifact_digest TEXT
);
CREATE TABLE IF NOT EXISTS render_segments (
    render_id INTEGER NOT NULL REFERENCES render_jobs(render_id),
    position INTEGER NOT NULL, cache_key TEXT NOT NULL,
    status TEXT NOT NULL, digest TEXT,
    PRIMARY KEY (render_id, position)
);
CREATE TABLE IF NOT EXISTS audio_voices (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    position INTEGER NOT NULL, asset_id INTEGER NOT NULL REFERENCES assets(id),
    start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
    at_ms INTEGER NOT NULL, gain REAL NOT NULL, duck_db REAL NOT NULL,
    UNIQUE(project_id, position)
);
CREATE TABLE IF NOT EXISTS audio_mix_plans (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id), revision INTEGER NOT NULL,
    payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS asset_links (
    asset_id INTEGER PRIMARY KEY REFERENCES assets(id), revision INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS asset_proxies (
    asset_id INTEGER PRIMARY KEY REFERENCES assets(id),
    generation INTEGER NOT NULL DEFAULT 0, ready_generation INTEGER,
    path TEXT, fingerprint TEXT, source_fingerprint TEXT, metadata TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS proxy_files (
    path TEXT PRIMARY KEY, asset_id INTEGER NOT NULL REFERENCES assets(id),
    fingerprint TEXT NOT NULL, size INTEGER NOT NULL, device INTEGER NOT NULL,
    inode INTEGER NOT NULL, created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS retime_plans (
    id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
    revision INTEGER NOT NULL, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transition_plans (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id), revision INTEGER NOT NULL,
    payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL
);
'''

def connect(path):
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def write(conn):
    if conn.in_transaction:
        raise ValueError('write requires an idle connection')
    conn.execute('BEGIN IMMEDIATE')
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
