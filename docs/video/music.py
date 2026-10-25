"""Downloads the films' music and finds its beats, so cuts can land on them.

    python music.py <out dir>

Writes <id>.wav for each track and beats.json: every beat and every bar's
first beat (the downbeat), in seconds. The tracks are royalty free from
Mixkit, under the Mixkit Stock Music Free License (no credit required; the
about page credits them anyway):

    470  "Golden Storm"        the promo, from the reveal on
    568  "Focus on Yourself"   how Sjekka works
    570  "Sonor #2"            how Sjekka was built

Needs numpy, soundfile and librosa, and ffmpeg."""
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

import librosa
import numpy as np

TRACKS = {"470": "Golden Storm", "568": "Focus on Yourself", "570": "Sonor #2"}
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)

beats = {}
for track, title in TRACKS.items():
    wav = OUT / f"{track}.wav"
    if not wav.exists():
        mp3 = OUT / f"{track}.mp3"
        request = urllib.request.Request(f"https://assets.mixkit.co/music/{track}/{track}.mp3", headers={"User-Agent": "Mozilla/5.0"})
        mp3.write_bytes(urllib.request.urlopen(request).read())
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp3), "-ac", "2", "-ar", "48000", str(wav)], check=True)
    y, sr = librosa.load(wav, sr=22050, mono=True)
    tempo, frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
    times = librosa.frames_to_time(frames, sr=sr)
    # The bar starts where the low end hits hardest: of the four ways to
    # group the beats in fours, the one with the most kick on its first beat
    low = librosa.onset.onset_strength(y=y, sr=sr, fmax=200)
    phase = max(range(4), key=lambda k: low[frames[k::4]].mean())
    beats[track] = {"title": title, "tempo": round(float(np.atleast_1d(tempo)[0]), 1), "seconds": round(len(y) / sr, 2),
                    "beats": [round(float(t), 3) for t in times], "downbeats": [round(float(t), 3) for t in times[phase::4]]}
    print(f"{track} {title}: {beats[track]['tempo']} bpm, {len(times)} beats")
(OUT / "beats.json").write_text(json.dumps(beats))
