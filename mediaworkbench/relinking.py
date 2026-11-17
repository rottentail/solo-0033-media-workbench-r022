"""Validate a batch of original relocations without holding the writer lock."""
from . import db, media_sources


def replace(conn, locations):
    if not isinstance(locations, list) or not 1 <= len(locations) <= 512:
        raise ValueError('locations must contain between 1 and 512 originals')
    prepared = []
    seen = set()
    for location in locations:
        if not isinstance(location, dict):
            raise ValueError('invalid original location')
        asset_id = location['asset_id']
        revision = location['expected_revision']
        if type(asset_id) is not int or asset_id <= 0 or asset_id in seen:
            raise ValueError('original identities must be positive and unique')
        if type(revision) is not int or revision < 0:
            raise ValueError('expected link revision must be nonnegative')
        seen.add(asset_id)
        asset = media_sources.get(conn, asset_id)
        if asset['link_revision'] != revision:
            raise ValueError('original location revision conflict')
        inspected = media_sources.inspect(location['path'])
        if inspected['fingerprint'] != asset['fingerprint']:
            raise ValueError('replacement does not contain the registered original')
        prepared.append((asset_id, revision, inspected))
    with db.write(conn):
        for asset_id, revision, inspected in prepared:
            if media_sources.get(conn, asset_id)['link_revision'] != revision:
                raise ValueError('original location revision conflict')
            if media_sources.stamp(inspected['path']) != inspected['stamp']:
                raise ValueError('replacement changed before reconnection')
        for asset_id, revision, inspected in prepared:
            conn.execute('UPDATE assets SET path=? WHERE id=?', (inspected['path'], asset_id))
            conn.execute('INSERT INTO asset_links(asset_id,revision) VALUES (?,?) '
                'ON CONFLICT(asset_id) DO UPDATE SET revision=excluded.revision',
                (asset_id, revision + 1))
    return [media_sources.get(conn, asset_id) for asset_id, _, _ in prepared]
