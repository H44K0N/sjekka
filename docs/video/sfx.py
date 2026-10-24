"""Writes the promo films' few, quiet sound effects as WAVs, synthesised here:

    click    a soft tap, under each click or tap
    pop      a small bubble, as each fake promo sign pops up
    whoosh   a short airy sweep between scenes
    boom     a low, soft thud under "declaring war"
    chime    two bright notes when Sjekka appears and when the answer shows

    python sfx.py <out dir>"""
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

RATE = 48000
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
rng = np.random.default_rng(3)


def t_of(seconds):
    return np.arange(int(seconds * RATE)) / RATE


def fade(x, attack=0.002, release=0.01):
    a, r = max(1, int(attack * RATE)), max(1, int(release * RATE))
    x[:a] *= np.linspace(0, 1, a)
    x[-r:] *= np.linspace(1, 0, r)
    return x


def smooth(x, width):
    return np.convolve(x, np.ones(width) / width, mode="same")


def click():
    t = t_of(0.04)
    return fade(0.6 * np.sin(2 * np.pi * 2400 * t) * np.exp(-t * 180) + 0.4 * smooth(rng.standard_normal(len(t)), 3) * np.exp(-t * 300))


def pop():
    t = t_of(0.09)
    f = 500 + 900 * np.exp(-t * 60)  # a quick upward blip, like a bubble
    return fade(np.sin(2 * np.pi * np.cumsum(f) / RATE) * np.exp(-t * 45))


def whoosh():
    t = t_of(0.55)
    noise = rng.standard_normal(len(t))
    # Swells and fades, and grows brighter as it passes
    shape = np.sin(np.pi * t / t[-1]) ** 2
    dull, bright = smooth(noise, 24), noise - smooth(noise, 4)
    mix = t / t[-1]
    return fade((1 - mix) * dull + 0.4 * mix * bright) * shape


def boom():
    t = t_of(1.2)
    tone = np.sin(2 * np.pi * np.cumsum(38 + 30 * np.exp(-t * 6)) / RATE)
    return fade(tone * np.exp(-t * 3.2), 0.004, 0.2)


def chime():
    t = t_of(1.4)
    out = np.zeros(len(t))
    for delay, f in ((0, 1046.5), (0.11, 1568.0)):  # C6 then G6
        start = int(delay * RATE)
        tt = t[: len(t) - start]
        out[start:] += (np.sin(2 * np.pi * f * tt) + 0.2 * np.sin(2 * np.pi * 2.76 * f * tt) * np.exp(-tt * 8)) * np.exp(-tt * 4)
    return fade(out, 0.002, 0.1)


for name, sound in dict(click=click(), pop=pop(), whoosh=whoosh(), boom=boom(), chime=chime()).items():
    sf.write(OUT / f"{name}.wav", (0.8 * sound / np.abs(sound).max()).astype(np.float32), RATE)
    print(OUT / f"{name}.wav")
