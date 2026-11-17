"""Exact bottleneck reading-rate optimization of bounded two-line captions."""
from fractions import Fraction
import unicodedata


def cells(text):
    return sum(0 if unicodedata.category(ch).startswith('M') else
               2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1 for ch in text)


def lines(words, width):
    text = [word['text'] for word in words]
    choices = []
    for split in range(1, len(text)+1):
        rows = [' '.join(text[:split])]
        if split < len(text):
            rows.append(' '.join(text[split:]))
        lengths = [cells(row) for row in rows]
        if all(length <= width for length in lengths):
            choices.append((sum((width-length)**2 for length in lengths), split, rows))
    return min(choices) if choices else None


def candidates(tokens, duration, width, minimum_ms, maximum_ms, hold_ms):
    edges = {index: [] for index in range(len(tokens))}
    for start, first in enumerate(tokens):
        for stop in range(start+1, min(len(tokens), start+32)+1):
            last = tokens[stop-1]
            if last['occurrence'] != first['occurrence']:
                break
            layout = lines(tokens[start:stop], width)
            if layout is None:
                continue
            next_start = tokens[stop]['start_ms'] if stop < len(tokens) else duration
            end = min(last['end_ms']+hold_ms, first['start_ms']+maximum_ms,
                      first['clip_end_ms'], next_start)
            if end < last['end_ms'] or end-first['start_ms'] < minimum_ms:
                continue
            cue = {'start_ms': first['start_ms'], 'end_ms': end, 'lines': layout[2],
                   'word_start': start, 'word_stop': stop, 'occurrence': first['occurrence']}
            units = sum(cells(word['text']) for word in tokens[start:stop])
            edges[start].append((stop, Fraction(units*1000, end-first['start_ms']), layout[0], cue))
    return edges


def solve(tokens, duration, width=32, max_cues=32, minimum_ms=400, maximum_ms=5000, hold_ms=500):
    for value, low, high in ((width, 4, 80), (max_cues, 1, 128), (minimum_ms, 1, 10000),
                             (maximum_ms, minimum_ms, 10000), (hold_ms, 0, 2000)):
        if type(value) is not int or not low <= value <= high:
            raise ValueError('caption width, count or duration bounds are invalid')
    if not tokens:
        return {'cues': [], 'worst_rate': [0, 1], 'raggedness': 0}
    edges = candidates(tokens, duration, width, minimum_ms, maximum_ms, hold_ms)
    # First find the smallest feasible bottleneck over the entire programme.
    costs = {(0, 0): Fraction(0)}
    for count in range(max_cues):
        for start in range(len(tokens)):
            if (count, start) not in costs:
                continue
            for stop, rate, _, _ in edges[start]:
                key = count+1, stop
                value = max(costs[count, start], rate)
                if key not in costs or value < costs[key]:
                    costs[key] = value
    terminal = [costs[count, len(tokens)] for count in range(1, max_cues+1)
                if (count, len(tokens)) in costs]
    if not terminal:
        raise ValueError('no complete caption layout fits the device limits')
    threshold = min(terminal)
    # A later bottleneck can hide an earlier prefix's rate. Recover secondary
    # objectives under the fixed optimal threshold, without pruning those ties.
    states = {(0, 0): (0, (), ())}
    for count in range(max_cues):
        for start in range(len(tokens)):
            previous = states.get((count, start))
            if previous is None:
                continue
            for stop, rate, penalty, cue in edges[start]:
                if rate > threshold:
                    continue
                value = (previous[0]+penalty, previous[1]+(stop,), previous[2]+(cue,))
                key = count+1, stop
                if key not in states or value[:2] < states[key][:2]:
                    states[key] = value
    finals = [(states[count, len(tokens)][0], count, states[count, len(tokens)][1],
               states[count, len(tokens)][2]) for count in range(1, max_cues+1)
              if (count, len(tokens)) in states]
    best = min(finals, key=lambda item: item[:3])
    return {'cues': list(best[3]), 'worst_rate': [threshold.numerator, threshold.denominator],
            'raggedness': best[0]}
