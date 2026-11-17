"""Convert first-order conventions and express a world field in listener axes."""
from array import array
import math
import sys
from . import spatial_plan


def canonical(frame, convention):
    if convention == 'ambix':
        w, y, z, x = frame
    elif convention == 'fuma':
        w, x, y, z = frame
        w *= math.sqrt(2)
    else:
        raise ValueError('unsupported soundfield convention')
    return w, x, y, z


def listener(frame, q):
    w, x, y, z = frame
    a, b, c, d = q
    # The listener-to-world pose acts inversely on the world soundfield.
    return (w,
        (1-2*(c*c+d*d))*x + 2*(b*c+a*d)*y + 2*(b*d-a*c)*z,
        2*(b*c-a*d)*x + (1-2*(b*b+d*d))*y + 2*(c*d+a*b)*z,
        2*(b*d+a*c)*x + 2*(c*d-a*b)*y + (1-2*(b*b+c*c))*z)


def transform(plan, pcm, start_sample):
    if len(pcm) % 4 or any(not math.isfinite(v) for v in pcm):
        raise ValueError('invalid four-channel samples')
    result = array('f')
    for index in range(len(pcm)//4):
        q = spatial_plan.orientation(plan, start_sample+index)
        w, x, y, z = listener(canonical(pcm[index*4:index*4+4], plan['convention']), q)
        result.extend((w, y, z, x))
    return result


def blocks(plan, handle, block_frames=1024, start_sample=0, samples=None):
    if type(block_frames) is not int or not 1 <= block_frames <= 65536:
        raise ValueError('invalid spatial block size')
    cursor = start_sample
    stop = plan['samples'] if samples is None else start_sample+samples
    if not 0 <= start_sample < stop <= plan['samples']:
        raise ValueError('invalid spatial sample interval')
    while cursor < stop:
        count = min(block_frames, stop-cursor)
        raw = handle.read(count*16)
        if len(raw) != count*16:
            raise ValueError('incomplete four-channel recording')
        pcm = array('f')
        pcm.frombytes(raw)
        if sys.byteorder != 'little':
            pcm.byteswap()
        output = transform(plan, pcm, cursor)
        if sys.byteorder != 'little':
            output.byteswap()
        yield output.tobytes()
        cursor += count
