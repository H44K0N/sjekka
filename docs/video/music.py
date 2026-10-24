"""Cuts the promo films' music from a recorded track, so that the moment the
track lifts lands on the story's reveal ("Meet Sjekka"): quiet under the
voice talking about the big chains, full as Sjekka appears.

    python music.py <seconds> <reveal seconds> <out.wav> <track.wav> <lift seconds>

promo.sh uses "Motivating Mornings" by Ahjay Stelino from Mixkit, free to
use under the Mixkit Stock Music Free License (no credit required); its
drums come in at 12.1 s. Needs numpy and soundfile (the voice-over venv
from make.sh has both)."""
import sys

import numpy as np
import soundfile as sf

SECONDS, REVEAL, OUT = float(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
TRACK, LIFT = sys.argv[4], float(sys.argv[5])

track, rate = sf.read(TRACK, always_2d=True)
track = track[:, :2] if track.shape[1] > 1 else np.repeat(track, 2, axis=1)
length = int(SECONDS * rate)
out = np.zeros((length, 2))

# Where in the track the film starts: the lift minus the time to the reveal.
# If the reveal comes later than the lift, the film starts in silence.
offset = int((LIFT - REVEAL) * rate)
src_start, dst_start = max(0, offset), max(0, -offset)
n = min(len(track) - src_start, length - dst_start)
out[dst_start: dst_start + n] = track[src_start: src_start + n]

# Ease in over half a second, and out over the last three
ramp = int(0.5 * rate)
out[:ramp] *= np.linspace(0, 1, ramp)[:, None]
tail = int(3.0 * rate)
out[-tail:] *= np.linspace(1, 0, tail)[:, None]

out /= np.abs(out).max() / 0.9
sf.write(OUT, out.astype(np.float32), rate)
print(OUT)
