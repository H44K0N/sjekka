"""Mixes a film's sound and encodes it with its frames.

    python mix.py <frames dir> <voice dir> <music dir> <out.mp4> [--fps 60] [--crf 20]

Reads film.json (written by render.py): when each voice line and sound
effect starts, and which music plays from where. The sound effects are
synthesised here (see SOUNDS), so there are no sound files to license. The
voice gets a broadcast chain (low cut, a little presence, compression), the
music ducks under it, and the whole mix is levelled to -16 LUFS, a common
level for the web. Also writes <out>.vtt, English captions from the script.

Needs numpy, soundfile and scipy, and ffmpeg with libx264."""
import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, resample_poly, sosfilt

RATE = 48000
rng = np.random.default_rng(11)


# ---------- sound design ----------

def t_of(seconds):
    return np.arange(int(seconds * RATE)) / RATE


def env(n, attack, release):
    e = np.ones(n)
    a, r = max(1, int(attack * RATE)), max(1, int(release * RATE))
    e[:a] = np.linspace(0, 1, a)
    e[-r:] *= np.linspace(1, 0, r)
    return e


def band(x, lo=None, hi=None, order=4):
    if lo and hi:
        sos = butter(order, [lo, hi], btype="band", fs=RATE, output="sos")
    elif lo:
        sos = butter(order, lo, btype="high", fs=RATE, output="sos")
    else:
        sos = butter(order, hi, btype="low", fs=RATE, output="sos")
    return sosfilt(sos, x)


def noise(seconds):
    return rng.standard_normal(int(seconds * RATE))


def sweep(f0, f1, seconds, curve=1.0):
    t = t_of(seconds)
    f = f0 + (f1 - f0) * (t / seconds) ** curve
    return np.sin(2 * np.pi * np.cumsum(f) / RATE)


def room(x, seconds=0.9, wet=0.18):
    """A small, dark room: convolution with decaying filtered noise."""
    ir = band(noise(seconds), hi=5000) * np.exp(-t_of(seconds) * 6 / seconds)
    ir /= np.abs(ir).sum() / 8
    tail = np.convolve(x, ir)[: len(x) + int(seconds * RATE)]
    out = np.zeros(len(tail))
    out[: len(x)] += x
    return out + wet * tail


def stereo(x, width=0.0):
    """Mono to stereo, with a slight delay on one side for width."""
    d = int(width * RATE)
    right = np.concatenate([np.zeros(d), x])[: len(x)] if d else x
    return np.stack([x, right], 1)


def norm(x, peak=0.9):
    m = np.abs(x).max()
    return x * (peak / m) if m else x


def s_click():
    t = t_of(0.05)
    return norm(0.7 * np.sin(2 * np.pi * 2600 * t) * np.exp(-t * 220) + 0.5 * band(noise(0.05), 2000, 9000) * np.exp(-t * 320))


def s_tap():
    t = t_of(0.07)
    return norm(0.8 * np.sin(2 * np.pi * np.cumsum(900 + 500 * np.exp(-t * 90)) / RATE) * np.exp(-t * 90)
                + 0.25 * band(noise(0.07), 1500, 6000) * np.exp(-t * 260))


def s_tick():
    t = t_of(0.025)
    return norm(band(noise(0.025), 3000, 8000) * np.exp(-t * 400))


def s_pop():
    t = t_of(0.1)
    return norm(sweep(450, 1400, 0.1, 0.4) * np.exp(-t * 40) * env(len(t), 0.002, 0.02))


def s_whoosh(seconds=0.7, bright=1.0):
    t = t_of(seconds)
    shape = np.sin(np.pi * t / seconds) ** 2
    n = noise(seconds)
    # A band that sweeps up as it passes
    out = np.zeros(len(t))
    for i, (lo, hi) in enumerate([(200, 900), (500, 2500), (1500, 7000 * bright)]):
        w = np.clip((t / seconds) * 3 - i + 0.5, 0, 1)
        out += band(n, lo, min(hi, 20000)) * w
    return norm(out * shape)


def s_swoosh():
    return norm(s_whoosh(0.4, 1.2))


def s_lift():
    t = t_of(0.5)
    e = env(len(t), 0.01, 0.08)
    return norm(band(noise(0.5), 800, 5000) * (t / 0.5) ** 1.5 * e + 0.3 * sweep(300, 900, 0.5) * (t / 0.5) * e)


def s_hit():
    t = t_of(0.8)
    body = sweep(110, 45, 0.8, 0.3) * np.exp(-t * 7)
    snap = band(noise(0.8), 800, 4000) * np.exp(-t * 60)
    return norm(room(body + 0.35 * snap, 0.8, 0.25))


def s_slam():
    """A sign slapped onto a board: a low punch and a papery crack."""
    t = t_of(0.45)
    punch = sweep(140, 48, 0.45, 0.25) * np.exp(-t * 11)
    crack = band(noise(0.45), 600, 5000) * np.exp(-t * 45)
    return norm(room(punch + 0.55 * crack, 0.6, 0.2))


def s_thud():
    t = t_of(0.6)
    return norm(room(sweep(90, 40, 0.6, 0.3) * np.exp(-t * 9), 0.6, 0.2))


def s_boom():
    """A deep cinematic boom with a long low tail."""
    t = t_of(3.0)
    sub = sweep(62, 30, 3.0, 0.2) * np.exp(-t * 1.4)
    grit = np.tanh(3 * sub) * 0.35
    rumble = band(noise(3.0), 30, 220) * np.exp(-t * 2.2) * 0.6
    crack = band(noise(3.0), 1000, 6000) * np.exp(-t * 25) * 0.4
    return norm(room(sub + grit + rumble + crack, 1.6, 0.3))


def s_riser():
    """Tension that ends on the reveal: noise opening up and a rising tone."""
    secs = 2.4
    t = t_of(secs)
    k = t / secs
    n = noise(secs)
    opening = np.zeros(len(t))
    for i, (lo, hi) in enumerate([(300, 1200), (900, 4000), (2500, 12000)]):
        opening += band(n, lo, hi) * np.clip(k * 3 - i, 0, 1)
    tone = sweep(180, 720, secs, 2.2) * 0.25 + sweep(270, 1080, secs, 2.2) * 0.15
    out = (opening * 0.7 + tone) * k ** 2.2
    cut = int(0.03 * RATE)
    out[-cut:] *= np.linspace(1, 0, cut)
    return norm(out)


def s_impact():
    """The reveal: a boom and a bright crash at once."""
    t = t_of(3.5)
    boom = np.concatenate([s_boom(), np.zeros(int(1.0 * RATE))])[: len(t)]
    crash = band(noise(3.5), 3000, 16000) * np.exp(-t * 2.5) * 0.35
    return norm(boom + crash)


def s_shimmer():
    t = t_of(2.2)
    out = np.zeros(len(t))
    for i, f in enumerate([1046.5, 1318.5, 1568.0, 2093.0, 2637.0]):
        start = int(i * 0.06 * RATE)
        tt = t[: len(t) - start]
        out[start:] += np.sin(2 * np.pi * f * tt) * np.exp(-tt * 2.2) * (0.7 ** i)
    return norm(room(out, 1.2, 0.35))


def s_chime():
    t = t_of(1.6)
    out = np.zeros(len(t))
    for delay, f in ((0, 1318.5), (0.1, 1975.5)):
        start = int(delay * RATE)
        tt = t[: len(t) - start]
        out[start:] += (np.sin(2 * np.pi * f * tt) + 0.2 * np.sin(2 * np.pi * 2.76 * f * tt) * np.exp(-tt * 8)) * np.exp(-tt * 3.5)
    return norm(room(out, 1.0, 0.3))


def s_drone():
    """A dark bed for the opening: low fifths that breathe, and air."""
    secs = 16.0
    t = t_of(secs)
    out = np.zeros(len(t))
    for f, a in ((55, 1.0), (55.4, 0.7), (82.4, 0.5), (110.3, 0.25)):
        out += a * np.sin(2 * np.pi * f * t + 0.5 * np.sin(2 * np.pi * 0.07 * t))
    air = band(noise(secs), 200, 1200) * 0.12 * (0.6 + 0.4 * np.sin(2 * np.pi * 0.11 * t))
    return norm((out * 0.4 + air) * env(len(t), 2.0, 3.0), 0.7)


SOUNDS = {name[2:]: fn for name, fn in dict(globals()).items() if name.startswith("s_")}


# ---------- the mix ----------

def load(path: Path) -> np.ndarray:
    x, rate = sf.read(path, always_2d=True)
    x = x.mean(1)
    return resample_poly(x, RATE, rate) if rate != RATE else x


def add(bus, x, at, gain=1.0):
    start = int(round(at * RATE))
    if x.ndim == 1:
        x = stereo(x)
    end = min(len(bus), start + len(x))
    if end > start:
        bus[start:end] += gain * x[: end - start]


def captions(film, out: Path):
    """WebVTT from the script: each line, split at sentences when long."""
    def stamp(s):
        return f"{int(s // 3600):02d}:{int(s % 3600 // 60):02d}:{s % 60:06.3f}"
    lines = ["WEBVTT", ""]
    for cue in film["cues"]:
        parts = [p.strip() for p in cue["text"].replace(". ", ".|").replace("? ", "?|").replace(": ", ":|").split("|") if p.strip()]
        total = sum(len(p) for p in parts)
        t = cue["at"]
        for p in parts:
            dur = cue["seconds"] * len(p) / total
            lines += [f"{stamp(t)} --> {stamp(t + dur)}", p, ""]
            t += dur
    out.write_text("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("frames")
    ap.add_argument("voice")
    ap.add_argument("music")
    ap.add_argument("out")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--crf", type=int, default=23)
    ap.add_argument("--out-fps", type=int, default=0, help="encode at this rate, dropping frames (default: --fps)")
    a = ap.parse_args()
    frames, out = Path(a.frames), Path(a.out)
    film = json.loads((frames / "film.json").read_text())
    n = int((film["duration"] + 0.5) * RATE)
    work = frames / "audio"
    work.mkdir(exist_ok=True)

    voice = np.zeros((n, 2))
    for cue in film["cues"]:
        add(voice, load(Path(a.voice) / film["name"] / f"{cue['key']}.wav"), cue["at"])
    sfx = np.zeros((n, 2))
    made = {}
    for e in film["sfx"]:
        if e["name"] not in made:
            made[e["name"]] = SOUNDS[e["name"]]()
        add(sfx, stereo(made[e["name"]], 0.008), e["at"], 0.5 * e["gain"])
    music = np.zeros((n, 2))
    m = film.get("music") or {}
    if m:
        track, rate = sf.read(Path(a.music) / f"{m['track']}.wav", always_2d=True)
        if rate != RATE:
            track = resample_poly(track, RATE, rate, axis=0)
        src = int(max(0, m["from"]) * RATE)
        dst = int((m["at"] + max(0, -m["from"])) * RATE)
        seg = track[src: src + n - dst].copy()
        fade = max(1, int(m.get("fadeIn", 0.05) * RATE))
        seg[:fade] *= np.linspace(0, 1, fade)[:, None]
        music[dst: dst + len(seg)] = seg * m.get("gain", 1.0)
        # Out over the last seconds of the film
        tail, end = int(m.get("fadeOut", 3.0) * RATE), int(film["duration"] * RATE)
        music[end - tail: end] *= np.linspace(1, 0, tail)[:, None]
        music[end:] = 0

    for name, bus in (("voice", voice), ("sfx", sfx), ("music", music)):
        sf.write(work / f"{name}.wav", bus.astype(np.float32), RATE, subtype="FLOAT")

    chain = ";".join([
        # Broadcast voice: low cut, less mud, a little presence, even level
        "[0:a]highpass=f=80,equalizer=f=250:t=q:w=1.2:g=-2,equalizer=f=3500:t=q:w=1.0:g=2.5,equalizer=f=9000:t=h:w=1:g=1.5,"
        "acompressor=threshold=-20dB:ratio=3:attack=6:release=90:makeup=3,asplit=2[vo][key]",
        "[1:a]volume=0.9[fx]",
        # The music dips while the voice speaks
        f"[2:a]volume={m.get('level', 0.5)}[bed0]",
        f"[bed0][key]sidechaincompress=threshold={m.get('duck', 0.03)}:ratio=5:attack=30:release=500[bed]",
        "[vo][fx][bed]amix=inputs=3:normalize=0,alimiter=limit=0.95:attack=5:release=60,loudnorm=I=-16:TP=-1.5:LRA=11[mix]",
    ])
    audio = work / "mix.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", work / "voice.wav", "-i", work / "sfx.wav", "-i", work / "music.wav",
                    "-filter_complex", chain, "-map", "[mix]", "-ar", str(RATE), str(audio)], check=True)

    w, h = film["width"], film["height"]
    fps = a.out_fps or a.fps
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", str(a.fps), "-i", str(frames / "%06d.jpg"), "-i", str(audio),
                    "-vf", f"fps={fps},scale={w}:{h}:flags=lanczos,format=yuv420p", "-c:v", "libx264", "-preset", "slow", "-crf", str(a.crf),
                    "-profile:v", "high", "-g", str(fps * 2),
                    "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out)], check=True)
    captions(film, out.with_suffix(".vtt"))
    print(f"Wrote {out} and {out.with_suffix('.vtt')}")


if __name__ == "__main__":
    main()
