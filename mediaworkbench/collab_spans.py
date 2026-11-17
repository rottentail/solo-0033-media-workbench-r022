"""Persistent source intervals with occurrence identity, independent of timeline offsets."""
import copy


def extent(spans):
    return sum(s['end_ms'] - s['start_ms'] for s in spans)


def split(spans, at):
    if type(at) is not int or not 0 <= at <= extent(spans):
        raise ValueError('edit position is outside its viewed timeline')
    before, after, cursor = [], [], 0
    for original in spans:
        span = copy.deepcopy(original)
        stop = cursor + span['end_ms'] - span['start_ms']
        if stop <= at:
            before.append(span)
        elif cursor >= at:
            after.append(span)
        else:
            left, right = copy.deepcopy(span), copy.deepcopy(span)
            left['end_ms'] = right['start_ms'] = span['start_ms'] + at - cursor
            before.append(left)
            after.append(right)
        cursor = stop
    return before, after


def cut(spans, start, end):
    if type(start) is not int or type(end) is not int or not start < end:
        raise ValueError('edit selection must have positive integer duration')
    prefix, rest = split(spans, start)
    selected, suffix = split(rest, end - start)
    return prefix, selected, suffix


def token(span, at):
    return {'uid': span['uid'], 'source_ms': at}


def gap(spans, at, known_birth=None):
    left, right = split(spans, at)
    return {'left': token(left[-1], left[-1]['end_ms'] - 1) if left else None,
            'right': token(right[0], right[0]['start_ms']) if right else None,
            'empty': not spans, 'known_birth': max((s.get('born', 0) for s in spans), default=0) if known_birth is None else known_birth}


def locate(spans, point):
    if point is None:
        return None
    cursor = 0
    found = []
    for span in spans:
        if span['uid'] == point['uid'] and span['start_ms'] <= point['source_ms'] < span['end_ms']:
            found.append(cursor + point['source_ms'] - span['start_ms'])
        cursor += span['end_ms'] - span['start_ms']
    if len(found) > 1:
        raise ValueError('ambiguous occurrence lineage')
    return found[0] if found else None


def resolve_gap(spans, anchor):
    left, right = locate(spans, anchor['left']), locate(spans, anchor['right'])
    if left is not None and right is not None:
        if left >= right:
            raise ValueError('edit destination was reordered')
        if right > left + 1:
            _, middle, _ = cut(spans, left + 1, right)
            if any(s.get('born', 0) <= anchor['known_birth'] for s in middle):
                raise ValueError('edit destination was split by moved content')
        return right
    if right is not None:
        return right
    if left is not None:
        return left + 1
    if anchor['empty'] and not spans:
        return 0
    raise ValueError('edit destination no longer exists')


def normalized(spans):
    result = []
    for original in spans:
        span = copy.deepcopy(original)
        if result and all(result[-1][k] == span[k] for k in ('uid', 'stamp', 'asset_id', 'gain')) and result[-1]['end_ms'] == span['start_ms']:
            result[-1]['end_ms'] = span['end_ms']
        else:
            result.append(span)
    return result


def resolve_selection(spans, expected):
    start = locate(spans, token(expected[0], expected[0]['start_ms']))
    if start is None:
        raise ValueError('selected content was removed')
    end = start + extent(expected)
    if end > extent(spans):
        raise ValueError('selected content is incomplete')
    prefix, actual, suffix = cut(spans, start, end)
    if normalized(actual) != normalized(expected):
        raise ValueError('selected content was concurrently changed')
    return prefix, actual, suffix
