"""Exact bounded-use music sequencing over directed musical joins."""

def choose(sections, joins, target_ms, overlap_ms, starts, ends):
    count = len(sections)
    costs = {(j['from'], j['to']): j['cost'] for j in joins}
    names = [s['name'] for s in sections]
    # A state retains resource consumption as well as the last musical phrase.
    # Duration follows from those counts and the number of overlaps.
    states = {}
    for i, section in enumerate(sections):
        if section['name'] in starts and section['duration_ms'] <= target_ms:
            used = tuple(int(k == i) for k in range(count))
            states[(used, i)] = (section['score'], (i,))
    best = None
    for depth in range(1, 13):
        following = {}
        for (used, last), (score, path) in states.items():
            elapsed = sum(n*s['duration_ms'] for n,s in zip(used,sections)) - (depth-1)*overlap_ms
            if elapsed == target_ms and names[last] in ends:
                candidate = (score, -depth, tuple(-i for i in path), path)
                if best is None or candidate[:3] > best[:3]:
                    best = candidate
            if elapsed >= target_ms or depth == 12:
                continue
            for nxt, section in enumerate(sections):
                edge = (names[last], names[nxt])
                if used[nxt] >= section['max_uses'] or edge not in costs:
                    continue
                if elapsed + section['duration_ms'] - overlap_ms > target_ms:
                    continue
                counts = list(used)
                counts[nxt] += 1
                key = (tuple(counts), nxt)
                value = (score + section['score'] - costs[edge], path+(nxt,))
                old = following.get(key)
                if old is None or value[0] > old[0] or (value[0] == old[0] and value[1] < old[1]):
                    following[key] = value
        states = following
    if best is None:
        raise ValueError('no complete musical sequence fits the requested duration')
    path = list(best[3])
    cursor = 0
    schedule = []
    for index in path:
        section = sections[index]
        schedule.append(dict(section, at_ms=cursor))
        cursor += section['duration_ms'] - overlap_ms
    return dict(sequence=schedule, score=best[0], duration_ms=target_ms, overlap_ms=overlap_ms)
