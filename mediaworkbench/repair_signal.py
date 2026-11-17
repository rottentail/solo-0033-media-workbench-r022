"""Joint-channel autoregressive interpolation of explicitly marked damage."""
import numpy as np

CONTEXT = 512
ORDER = 24


def read(handle, first, count):
    handle.seek(first*8)
    raw = handle.read(count*8)
    if len(raw) != count*8:
        raise ValueError('incomplete repair context')
    pcm = np.frombuffer(raw, dtype='<f4').reshape(-1, 2).astype(np.float64)
    if not np.isfinite(pcm).all():
        raise ValueError('nonfinite repair context')
    return pcm


def predictor(context):
    # Pool equations, not channel samples: antiphase information cannot cancel.
    rows = np.stack([context[ORDER-k-1:len(context)-k-1] for k in range(ORDER)],axis=2)
    matrix = rows.reshape(-1, ORDER)
    target = context[ORDER:].reshape(-1)
    gram = matrix.T@matrix
    ridge = max(float(np.trace(gram))/ORDER*1e-11, 1e-12)
    return np.linalg.solve(gram+ridge*np.eye(ORDER), matrix.T@target)


def restore(left, right, length):
    if np.max(abs(left)) < 1e-10 and np.max(abs(right)) < 1e-10:
        return np.zeros((length,2), dtype='<f4')
    # Suppress only numerically null stereo directions before the ill-conditioned
    # gap solve; keep signed channel geometry instead of independent gains.
    context = np.concatenate((left,right))
    energies, basis = np.linalg.eigh(context.T@context)
    retained = energies > max(float(energies[-1])*1e-12,1e-20)
    projection = basis[:,retained]@basis[:,retained].T
    left, right = left@projection, right@projection
    forward, backward = predictor(left), predictor(right[::-1])
    width = length+2*ORDER
    known = np.concatenate((left[-ORDER:],np.zeros((length,2)),right[:ORDER]))
    equations, values = [], []
    # Fit forward and backward residuals together. Known boundary samples are
    # constraints, not generated predictions that can drift into the next take.
    for direction, coefficients in ((1,forward),(-1,backward)):
        for n in range(ORDER, width) if direction==1 else range(0,ORDER+length):
            row = np.zeros(length)
            rhs = np.zeros(2)
            for index, weight in [(n,1.0)]+[(n-direction*(k+1),-v) for k,v in enumerate(coefficients)]:
                if not 0 <= index < width:
                    continue
                if ORDER <= index < ORDER+length:
                    row[index-ORDER] += weight
                else:
                    rhs -= weight*known[index]
            equations.append(row);values.append(rhs)
    matrix, target = np.asarray(equations), np.asarray(values)
    gram = matrix.T@matrix
    result = np.linalg.solve(gram+1e-13*np.eye(length),matrix.T@target)
    if not np.isfinite(result).all() or np.max(abs(result))>8*max(float(np.max(abs(left))),float(np.max(abs(right))),1e-6):
        raise ValueError('repair reconstruction failed')
    return result.astype('<f4')


def patches(handle, intervals):
    restored=[]
    for first,stop in intervals:
        left=read(handle,first-CONTEXT,CONTEXT)
        right=read(handle,stop,CONTEXT)
        restored.append((first,stop,restore(left,right,stop-first)))
    return restored


def blocks(handle, clip, restored, first, stop, block_frames):
    origin=clip['start_ms']*48
    for cursor in range(first,stop,block_frames):
        end=min(stop,cursor+block_frames)
        pcm=read(handle,origin+cursor,end-cursor)
        for left,right,patch in restored:
            a,b=max(origin+cursor,left),min(origin+end,right)
            if a<b: pcm[a-origin-cursor:b-origin-cursor]=patch[a-left:b-left]
        yield (pcm*clip['gain']).astype('<f4')
