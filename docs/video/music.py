"""Writes a calm background track for the promo videos: a slow lo-fi loop in
D major (pad, plucked arpeggio, bass, soft kick and shaker), synthesised
here so there is no licence to worry about.

    python music.py <seconds> <out.wav>

Needs numpy and soundfile (the voice-over venv from make.sh has both)."""
import sys

import numpy as np
import soundfile as sf

SECONDS, OUT = float(sys.argv[1]), sys.argv[2]
RATE = 48000
BPM = 88
BEAT = 60 / BPM
BAR = 4 * BEAT
rng = np.random.default_rng(7)

# Dmaj7, Bm7, Gmaj7, A6: two bars each, as MIDI notes
CHORDS = [[50, 54, 57, 61], [47, 50, 54, 57], [43, 47, 50, 54], [45, 49, 52, 54]]


def hz(note):
    return 440 * 2 ** ((note - 69) / 12)


def envelope(n, attack, release):
    env = np.ones(n)
    a, r = int(attack * RATE), int(release * RATE)
    env[:a] = np.linspace(0, 1, a)
    env[-r:] *= np.linspace(1, 0, r)
    return env


def lowpass(x, cutoff):
    """One-pole low-pass, enough to take the edge off."""
    alpha = 1 - np.exp(-2 * np.pi * cutoff / RATE)
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc += alpha * (v - acc)
        y[i] = acc
    return y


def pad(notes, seconds):
    t = np.arange(int(seconds * RATE)) / RATE
    tone = sum(np.sin(2 * np.pi * hz(n + 12) * d * t) for n in notes for d in (0.997, 1.0, 1.003))
    return tone / (3 * len(notes)) * envelope(len(t), 0.8, 1.2)


def pluck(note, seconds=1.2):
    t = np.arange(int(seconds * RATE)) / RATE
    f = hz(note + 24)
    tone = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(4 * np.pi * f * t) + 0.12 * np.sin(6 * np.pi * f * t)
    return tone * np.exp(-t * 5) * envelope(len(t), 0.012, 0.05)


def bass(note, seconds):
    t = np.arange(int(seconds * RATE)) / RATE
    return np.sin(2 * np.pi * hz(note - 12) * t) * envelope(len(t), 0.02, 0.3) * np.exp(-t * 1.2)


def kick():
    t = np.arange(int(0.35 * RATE)) / RATE
    sweep = 2 * np.pi * np.cumsum(50 + 70 * np.exp(-t * 30)) / RATE
    return np.sin(sweep) * np.exp(-t * 11)


def shaker():
    n = int(0.06 * RATE)
    noise = rng.standard_normal(n)
    noise = lowpass(noise - lowpass(noise, 4000), 9000)  # a soft hiss: no thump, no sizzle
    return noise * envelope(n, 0.008, 0.02) * np.exp(-np.arange(n) / RATE * 60)


def place(track, sound, at, gain):
    start = int(at * RATE)
    end = min(len(track), start + len(sound))
    if start < end:
        track[start:end] += gain * sound[: end - start]


length = int(SECONDS * RATE)
pads, plucks, low, drums = (np.zeros(length) for _ in range(4))
bar = 0
while bar * BAR < SECONDS:
    chord = CHORDS[(bar // 2) % len(CHORDS)]
    start = bar * BAR
    if bar % 2 == 0:
        place(pads, pad(chord, 2 * BAR + 1.2), start, 1.0)
    # An eighth-note arpeggio, up and back down
    pattern = [0, 1, 2, 3, 2, 1, 2, 3]
    for i, step in enumerate(pattern):
        place(plucks, pluck(chord[step]), start + i * BEAT / 2, 0.9 if i % 2 == 0 else 0.6)
    place(low, bass(chord[0], 1.6 * BEAT), start, 1.0)
    place(low, bass(chord[0], 1.6 * BEAT), start + 2.5 * BEAT, 0.7)
    for b in range(4):
        if b in (0, 2) and bar > 1:  # the beat comes in after two bars
            place(drums, kick(), start + b * BEAT, 1.0)
        place(drums, shaker(), start + b * BEAT + BEAT / 2, 0.15)
    bar += 1

plucks = lowpass(plucks, 3200)
mix = 0.6 * pads + 0.38 * plucks + 0.22 * low + 0.2 * drums

# A little room: two quiet echoes, slightly different left and right
left, right = mix.copy(), mix.copy()
for delay, gain in ((0.23, 0.22), (0.41, 0.12)):
    d = int(delay * RATE)
    left[d:] += gain * mix[:-d]
    d2 = int((delay + 0.03) * RATE)
    right[d2:] += gain * mix[:-d2]
stereo = np.stack([left, right], axis=1)
stereo *= envelope(length, 1.5, 3.0)[:, None]
stereo /= np.abs(stereo).max() / 0.8
sf.write(OUT, stereo.astype(np.float32), RATE)
print(OUT)
