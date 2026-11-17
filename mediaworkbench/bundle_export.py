"""Write one bounded portable archive from frozen, retained proxy receipts."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import tempfile
import zipfile
from . import bundle_plan, bundle_select
from .assets import fingerprint


def write_media(archive, name, receipt):
    digest = hashlib.sha256()
    count = 0
    with open(receipt['path'], 'rb') as source, archive.open(name, 'w', force_zip64=True) as output:
        for chunk in iter(lambda: source.read(1024*1024), b''):
            digest.update(chunk)
            count += len(chunk)
            output.write(chunk)
    if count != receipt['size'] or digest.hexdigest() != receipt['fingerprint']:
        raise ValueError('proxy changed while creating the offline package')
    return count


def create(conn, projects, max_media_bytes, destination):
    if type(max_media_bytes) is not int or max_media_bytes < 0:
        raise ValueError('media capacity must be a nonnegative integer')
    destination = Path(destination).expanduser().absolute()
    destination = destination.parent.resolve()/destination.name
    if os.path.lexists(destination) or not destination.parent.is_dir():
        raise ValueError('destination exists or its directory is missing')
    with ExitStack() as stack:
        plan = bundle_plan.capture(conn, projects, stack)
        sizes = {key: value['size'] for key, value in plan['media'].items()}
        selection = bundle_select.choose(plan['projects'], sizes, max_media_bytes)
        keep = {plan['media'][key]['path'] for key in selection['media']}
        for path, handle in plan['locks'].items():
            if path not in keep:
                handle.close()
        description = bundle_plan.manifest(plan, selection)
        with tempfile.TemporaryDirectory(prefix='.offline-bundle-', dir=destination.parent) as directory:
            output = Path(directory)/'package.zip'
            actual = 0
            with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
                for key in selection['media']:
                    actual += write_media(archive, 'media/'+key+'.mp4', plan['media'][key])
                if actual > max_media_bytes or actual != selection['media_bytes']:
                    raise ValueError('package byte allocation no longer matches its contents')
                archive.writestr('manifest.json', json.dumps(description, sort_keys=True,
                    ensure_ascii=False, separators=(',', ':'), allow_nan=False))
            with output.open('rb') as handle:
                os.fsync(handle.fileno())
            checksum = fingerprint(output)
            os.link(output, destination)
    return {'output_path': str(destination), 'sha256': checksum,
            'project_ids': selection['project_ids'], 'value': selection['value'],
            'media_bytes': actual, 'unavailable': plan['unavailable']}


def write_fanout(archives, name, receipt):
    """One source traversal, possibly several independent destination archives."""
    digest = hashlib.sha256()
    count = 0
    with ExitStack() as streams:
        source = streams.enter_context(open(receipt['path'], 'rb'))
        outputs = [streams.enter_context(archive.open(name, 'w', force_zip64=True))
                   for archive in archives]
        for chunk in iter(lambda: source.read(1024*1024), b''):
            digest.update(chunk)
            count += len(chunk)
            for output in outputs:
                output.write(chunk)
    if count != receipt['size'] or digest.hexdigest() != receipt['fingerprint']:
        raise ValueError('proxy changed while creating the offline volume set')
    return count


def withdraw(path, device, inode):
    """Inspect a withdrawn name privately before deleting owned bytes."""
    directory = Path(tempfile.mkdtemp(prefix='.offline-recovery-', dir=path.parent))
    candidate = directory/'candidate'
    try:
        try:
            os.rename(path, candidate)
        except FileNotFoundError:
            return None
        info = candidate.lstat()
        if (info.st_dev, info.st_ino) != (device, inode):
            # An unrelated writer may replace a published name at any time.
            # Put that inode back without overwriting a newer replacement.
            try:
                os.link(candidate, path)
            except FileExistsError:
                return str(candidate)
        candidate.unlink()
        return None
    finally:
        if not candidate.exists():
            directory.rmdir()


def create_set(conn, projects, volumes):
    if (not isinstance(volumes, list) or not 1 <= len(volumes) <= 3
            or not isinstance(projects, list) or len(projects) > 12):
        raise ValueError('a set needs one to three volumes and at most twelve projects')
    destinations = []
    capacities = []
    for volume in volumes:
        capacity = volume['max_media_bytes']
        if type(capacity) is not int or capacity < 0:
            raise ValueError('volume capacity must be a nonnegative integer')
        path = Path(volume['destination']).expanduser().absolute()
        path = path.parent.resolve()/path.name
        if os.path.lexists(path) or not path.parent.is_dir() or path in destinations:
            raise ValueError('volume destinations must be distinct new files in existing directories')
        destinations.append(path)
        capacities.append(capacity)
    published = []
    try:
        with ExitStack() as stack:
            plan = bundle_plan.capture(conn, projects, stack)
            sizes = {k: receipt['size'] for k,receipt in plan['media'].items()}
            allocation = bundle_select.distribute(plan['projects'], sizes, capacities)
            selected = {key for volume in allocation['volumes'] for key in volume['media']}
            keep = {plan['media'][key]['path'] for key in selected}
            for path,handle in plan['locks'].items():
                if path not in keep:
                    handle.close()
            outputs = []
            archives = []
            for destination in destinations:
                directory = Path(stack.enter_context(tempfile.TemporaryDirectory(
                    prefix='.offline-volume-', dir=destination.parent)))
                output = directory/'volume.zip'
                outputs.append(output)
                archives.append(stack.enter_context(zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED)))
            for key in sorted(selected):
                owners = [archives[i] for i,volume in enumerate(allocation['volumes']) if key in volume['media']]
                write_fanout(owners, 'media/'+key+'.mp4', plan['media'][key])
            for i,archive in enumerate(archives):
                archive.writestr('manifest.json', json.dumps(bundle_plan.volume_manifest(plan, allocation, i),
                    ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False))
                archive.close()
            for output in outputs:
                with output.open('rb') as handle:
                    os.fsync(handle.fileno())
            checksums = [fingerprint(output) for output in outputs]
            for output,destination in zip(outputs,destinations):
                info = output.stat()
                os.link(output, destination)
                published.append((destination, info.st_dev, info.st_ino))
        return dict(allocation, outputs=[{'path':str(p),'sha256':checksum}
                                         for p,checksum in zip(destinations,checksums)])
    except BaseException as exc:
        recoveries = []
        for path,device,inode in published:
            retained = withdraw(path, device, inode)
            if retained:
                recoveries.append(retained)
        if recoveries:
            raise ValueError('concurrent replacements preserved at '+', '.join(recoveries)) from exc
        raise
