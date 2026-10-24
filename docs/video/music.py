"""Writes the promo films' background track, synthesised here so there is no
licence to worry about. It follows the story: a quiet, tense pulse while the
voice talks about the big chains, then, from `reveal` ("Meet Sjekka"), a
bright and warm groove in C major. Short plucks and no long reverb tails,
so it stays out of the voice's way.

    python music.py <seconds> <reveal seconds> <out.wav>

Needs numpy and soundfile (the voice-over venv from make.sh has both)."""
import sys

import numpy as np
import soundfile as sf

SECONDS, REVEAL, OUT = float(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
RATE = 48000
BPM = 104
BEAT = 60 / BPM
BAR = 4 * BEAT
rng = np.random.default_rng(11)

# C, G, Am, F: the brightest of the common progressions
CHORDS = [[48, 52, 55, 60], [43, 47, 50, 55], [45, 48, 52, 57], [41, 45, 48, 53]]


def hz(note):
    return 440 * 2 ** ((note - 69) / 12)


def t_of(seconds):
    return np.arange(int(seconds * RATE)) / RATE


def fade(n, attack, release):
    env = np.ones(n)
    a, r = max(1, int(attack * RATE)), max(1, int(release * RATE))
    env[:a] = np.linspace(0, 1, a)
    env[-r:] *= np.linspace(1, 0, r)
    return env


def marimba(note, seconds=0.5):
    """A wooden mallet: the note plus a quiet fourth harmonic, gone in half a second."""
    t = t_of(seconds)
    f = hz(note)
    tone = np.sin(2 * np.pi * f * t) + 0.25 * np.sin(2 * np.pi * 4 * f * t) * np.exp(-t * 30)
    return tone * np.exp(-t * 9) * fade(len(t), 0.003, 0.03)


def bass(note, seconds):
    t = t_of(seconds)
    f = hz(note - 12)
    tone = np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t)
    return tone * np.exp(-t * 2.5) * fade(len(t), 0.01, 0.08)


def kick():
    t = t_of(0.25)
    return np.sin(2 * np.pi * np.cumsum(55 + 90 * np.exp(-t * 35)) / RATE) * np.exp(-t * 16)


def noise_hit(seconds, decay, bright):
    n = int(seconds * RATE)
    x = rng.standard_normal(n)
    if not bright:  # a duller, wooden version: average neighbouring samples
        x = np.convolve(x, np.ones(6) / 6, mode="same")
    return x * np.exp(-np.arange(n) / RATE * decay) * fade(n, 0.002, 0.01)


def clap():
    return sum(np.roll(noise_hit(0.12, 35, False), int(d * RATE)) for d in (0, 0.008, 0.017)) / 3


def tick():
    t = t_of(0.03)
    return np.sin(2 * np.pi * 1800 * t) * np.exp(-t * 200)


def place(track, sound, at, gain):
    start = int(at * RATE)
    end = min(len(track), start + len(sound))
    if 0 <= start < end:
        track[start:end] += gain * sound[: end - start]


length = int(SECONDS * RATE)
music = np.zeros(length)

# Before the reveal: a clock ticking and a low, muffled pulse on every other beat
beat = 0
while beat * BEAT < REVEAL - 0.05:
    at = beat * BEAT
    place(music, tick(), at, 0.16 if beat % 2 else 0.22)
    if beat % 2 == 0:
        place(music, bass(36, 0.5), at, 0.22)
    beat += 1

# After: the groove, starting on the reveal
bar = 0
while REVEAL + bar * BAR < SECONDS:
    start = REVEAL + bar * BAR
    chord = CHORDS[bar % len(CHORDS)]
    # A bouncing mallet line in eighths
    for i, idx in enumerate([0, 2, 3, 1, 2, 3, 1, 2]):
        place(music, marimba(chord[idx] + 24), start + i * BEAT / 2, 0.30 if i % 2 == 0 else 0.20)
    place(music, bass(chord[0], BEAT * 1.5), start, 0.28)
    place(music, bass(chord[0], BEAT * 1.0), start + 2.5 * BEAT, 0.20)
    for b in range(4):
        place(music, kick(), start + b * BEAT, 0.28 if b in (0, 2) else 0.15)
        if b in (1, 3):
            place(music, clap(), start + b * BEAT, 0.16)
        place(music, noise_hit(0.04, 90, True), start + b * BEAT + BEAT / 2, 0.035)
    bar += 1

# Slightly wider than mono: the right channel partly a few milliseconds late
d = int(0.012 * RATE)
right = np.concatenate([np.zeros(d), music[:-d]])
stereo = np.stack([music, 0.9 * music + 0.1 * right], axis=1)
stereo *= fade(length, 0.8, 2.5)[:, None]
stereo /= np.abs(stereo).max() / 0.8
sf.write(OUT, stereo.astype(np.float32), RATE)
print(OUT)
