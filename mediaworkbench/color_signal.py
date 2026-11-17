"""Absolute-light decoding, scene exposure and luminance-preserving SDR mapping."""
import numpy as np

LUMA = np.array([.2126, .7152, .0722])
TO_709 = np.array([[1.660491, -.587641, -.072850],
                   [-.124550, 1.132900, -.008349],
                   [-.018151, -.100579, 1.118730]])


def decode(rgb, encoding):
    values = np.asarray(rgb, dtype=float)
    if values.shape[-1] != 3 or not np.all(np.isfinite(values)):
        raise ValueError('finite three-channel colour required')
    if encoding == 'pq2020':
        m1, m2 = 2610 / 16384, 2523 / 32
        power = np.maximum(values, 0) ** (1 / m2)
        linear = (np.maximum(power - 3424 / 4096, 0)
                  / np.maximum(2413 / 128 - 2392 / 128 * power, 1e-12)) ** (1 / m1) * 10000
    elif encoding == 'linear2020':
        linear = values * 1000
    elif encoding == 'srgb709':
        return np.where(values <= .04045, values / 12.92,
                        ((values + .055) / 1.055) ** 2.4) * 100
    else:
        raise ValueError('unsupported source colour encoding')
    return linear @ TO_709.T


def exposure(histogram, white_nits):
    counts = np.asarray(histogram, dtype=float)
    total = counts.sum()
    if total == 0:
        return 1.
    cumulative = np.cumsum(counts)
    # Partial boundary bins make trimming independent of the decoder block size.
    included = np.maximum(0, np.minimum(cumulative, total * .95)
                          - np.maximum(cumulative - counts, total * .05))
    centres = -12 + (np.arange(1024) + .5) * 28 / 1024
    average = 2 ** (float(np.dot(centres, included)) / max(included.sum(), 1))
    return min(64., max(.001, .18 * white_nits / average))


def accumulate(histogram, linear):
    luminance = np.maximum(linear @ LUMA, 0)
    positive = luminance[luminance > 1e-8]
    bins = np.clip(((np.log2(positive) + 12) * 1024 / 28).astype(int), 0, 1023)
    histogram += np.bincount(bins, minlength=1024)


def map_frame(linear, gain, white_nits):
    luminance = np.maximum(linear @ LUMA, 0)
    level = luminance * gain / white_nits
    # The shoulder is continuous with unit slope; ordinary middle tones stay linear.
    mapped = np.where(level <= .6, level,
                      .6 + .4 * np.maximum(level - .6, 0) / (.4 + np.maximum(level - .6, 0)))
    scaled = linear * (mapped / np.maximum(luminance, 1e-12))[..., None]
    direction = scaled - mapped[..., None]
    # Intersect the hue ray with the SDR cube while retaining its luminance.
    upper = np.where(direction > 1e-14,
        (1 - mapped[..., None]) / np.maximum(direction, 1e-14), np.inf)
    lower = np.where(direction < -1e-14,
        -mapped[..., None] / np.minimum(direction, -1e-14), np.inf)
    saturation = np.minimum(1, np.minimum(upper, lower).min(axis=-1))
    compressed = mapped[..., None] + direction * saturation[..., None]
    return np.clip(compressed, 0, 1)


def encode(linear):
    return np.where(linear <= .0031308, linear * 12.92,
                    1.055 * np.maximum(linear, 0) ** (1 / 2.4) - .055)
