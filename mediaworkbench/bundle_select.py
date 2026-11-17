"""Allocate a shared byte budget to complete offline projects, not single clips."""


def choose(projects, sizes, capacity):
    if type(capacity) is not int or capacity < 0:
        raise ValueError('media capacity must be a nonnegative integer')
    if len(projects) > 16:
        raise ValueError('at most sixteen candidate projects are supported')
    if any(type(n) is not int or n <= 0 for n in sizes.values()):
        raise ValueError('rendition sizes must be positive integers')
    identities = [p['id'] for p in projects]
    if len(set(identities)) != len(identities):
        raise ValueError('candidate projects must be unique')
    required = [frozenset(p['required']) for p in projects]
    if any(not group or not group <= sizes.keys() for group in required):
        raise ValueError('every project requires available media')
    if any(type(p['priority']) is not int or p['priority'] <= 0 for p in projects):
        raise ValueError('project priorities must be positive integers')
    # Every useful package is the union of one or more whole projects. Shared
    # renditions consume bytes once; selecting them can complete other projects.
    unions = {frozenset(): 0}
    for group in required:
        for previous, cost in list(unions.items()):
            combined = previous | group
            if combined not in unions:
                total = cost + sum(sizes[key] for key in group - previous)
                if total <= capacity:
                    unions[combined] = total
    best = (0, 0, (), ())
    for media, cost in unions.items():
        covered = tuple(sorted(p['id'] for p, group in zip(projects, required) if group <= media))
        value = sum(p['priority'] for p in projects if p['id'] in covered)
        candidate = (-value, cost, covered, tuple(sorted(media)))
        if candidate < best:
            best = candidate
    return {'project_ids': list(best[2]), 'media': list(best[3]),
            'value': -best[0], 'media_bytes': best[1]}


def distribute(projects, sizes, capacities):
    """Choose independent media unions for each physical offline volume."""
    if (not isinstance(capacities, list) or not 1 <= len(capacities) <= 3
            or any(type(n) is not int or n < 0 for n in capacities)):
        raise ValueError('one to three nonnegative volume capacities required')
    if len(projects) > 12:
        raise ValueError('a volume set supports at most twelve projects')
    choose(projects, sizes, 0)
    groups = [frozenset(p['required']) for p in projects]
    unions = {frozenset(): 0}
    for group in groups:
        for media, cost in list(unions.items()):
            combined = media | group
            if combined not in unions:
                size = cost + sum(sizes[k] for k in group-media)
                if size <= max(capacities):
                    unions[combined] = size
    options = []
    for capacity in capacities:
        covered = {}
        for media, cost in unions.items():
            if cost > capacity:
                continue
            mask = sum(1 << i for i, group in enumerate(groups) if group <= media)
            value = (cost, tuple(sorted(media)))
            if mask not in covered or value < covered[mask]:
                covered[mask] = value
        options.append(covered)
    # Capacity and sharing apply within each volume. Combining only coverage
    # masks is safe after each volume has retained its cheapest exact coverage.
    states = {0: (0, ())}
    for choices in options:
        following = {}
        for previous, (spent, placement) in states.items():
            for coverage, (cost, media) in choices.items():
                combined = previous | coverage
                candidate = (spent+cost, placement+(media,))
                if combined not in following or candidate < following[combined]:
                    following[combined] = candidate
        states = following
    best = None
    for mask, (cost, placement) in states.items():
        ids = tuple(sorted(p['id'] for i,p in enumerate(projects) if mask >> i & 1))
        value = sum(p['priority'] for i,p in enumerate(projects) if mask >> i & 1)
        candidate = (-value, cost, ids, placement)
        if best is None or candidate < best:
            best = candidate
    assignments = {}
    for project, group in zip(projects, groups):
        for volume, media in enumerate(best[3]):
            if group <= set(media):
                assignments[project['id']] = volume
                break
    volumes = []
    for volume, media in enumerate(best[3]):
        ids = sorted(pid for pid,index in assignments.items() if index == volume)
        volumes.append({'project_ids': ids, 'media': list(media),
            'media_bytes': sum(sizes[k] for k in media),
            'value': sum(p['priority'] for p in projects if p['id'] in ids)})
    return {'project_ids': list(best[2]), 'value': -best[0],
            'media_bytes': best[1], 'volumes': volumes}
