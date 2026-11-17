"""Partitioned causal matrix convolution on the programme excitation clock."""
from array import array
from collections import deque
from . import audio_signal, room_plan
from .denoise_signal import fft

PARTITION = 512


def weights(stages, sample):
    result = [0.0]*len(stages)
    active = 0
    while active+1<len(stages) and sample>=stages[active+1]['at']:
        active += 1
    stage = stages[active]
    if active and stage['fade'] and sample < stage['at']+stage['fade']:
        fraction = (sample-stage['at'])/stage['fade']
        result[active-1], result[active] = 1-fraction, fraction
    else:
        result[active] = 1.0
    return result


def partitions(taps):
    size = PARTITION
    return [fft(taps[i:i+size]+[0.0]*(2*size-len(taps[i:i+size])))
            for i in range(0,len(taps),size)]


class Matrix:
    def __init__(self, kernels):
        self.filters = [partitions(taps) for taps in kernels]
        self.history = [deque(maxlen=len(self.filters[0])) for _ in range(2)]
        self.overlap = [[0.0]*PARTITION for _ in range(2)]

    def push(self, channels):
        size = PARTITION
        for channel in range(2):
            self.history[channel].appendleft(fft(channels[channel]+[0.0]*size))
        output = []
        for out in range(2):
            bins = [0j]*(2*size)
            for source in range(2):
                for kernel, excitation in zip(self.filters[out*2+source],self.history[source]):
                    for k in range(2*size):
                        bins[k] += kernel[k]*excitation[k]
            restored = fft(bins,True)
            output.append([restored[k].real+self.overlap[out][k] for k in range(size)])
            self.overlap[out] = [v.real for v in restored[size:]]
        return output


def blocks(source, plan, block_frames=4096):
    if type(block_frames) is not int or not 1<=block_frames<=65536:
        raise ValueError('invalid room output block size')
    matrices = [Matrix(room_plan.kernels(s['response'])) for s in plan['stages']]
    tail = max(s['response']['frames'] for s in plan['stages'])-1
    total = plan['frames']+tail
    pending = array('f')
    for origin in range(0,total,PARTITION):
        count = min(PARTITION,max(0,plan['frames']-origin))
        pcm = audio_signal.read_frames(source,count)
        if len(pcm)!=count*2:
            raise ValueError('programme spool truncated')
        pcm.extend([0.0]*(PARTITION*2-len(pcm)))
        inputs = [[[0.0]*PARTITION for _ in range(2)] for _ in matrices]
        for local in range(PARTITION):
            coefficients = weights(plan['stages'],origin+local)
            for stage, coefficient in enumerate(coefficients):
                for channel in range(2):
                    inputs[stage][channel][local] = pcm[local*2+channel]*coefficient
        wet = [matrix.push(signal) for matrix,signal in zip(matrices,inputs)]
        for local in range(min(PARTITION,total-origin)):
            for channel in range(2):
                pending.append(plan['dry']*pcm[local*2+channel]+
                    plan['wet']*sum(room[channel][local] for room in wet))
        while len(pending)>=block_frames*2:
            yield pending[:block_frames*2]
            del pending[:block_frames*2]
    if pending:
        yield pending
