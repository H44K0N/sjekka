"""Mixes a promo film: the voice lines and sound effects at the moments
promo.py logged, over music.py's track, which ducks under the voice.

    python promo_mix.py <raw video> <promo-mode.json> <voice dir> <sfx dir> <music.wav> <out.mp4>"""
import json
import subprocess
import sys

video, log_file, voice, sfx_dir, music, out = sys.argv[1:7]
TRIM = 0.4  # blank frames at the very start of the recording
log = json.loads(open(log_file).read())
# Effects stay well under the voice: felt more than heard
GAIN = {"click": 0.22, "pop": 0.28, "whoosh": 0.3, "boom": 0.35, "chime": 0.3}

cmd = ["ffmpeg", "-v", "error", "-y", "-ss", str(TRIM), "-i", video, "-i", music]
filters, voices, effects = [], [], []
n = 2
for cue, start in log["cues"].items():
    if cue == "reveal":
        continue
    cmd += ["-i", f"{voice}/{cue}.wav"]
    filters.append(f"[{n}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={max(0, round((start - TRIM) * 1000))}:all=1[v{n}]")
    voices.append(f"[v{n}]")
    n += 1
for name, start in log["sfx"]:
    cmd += ["-i", f"{sfx_dir}/{name}.wav"]
    filters.append(f"[{n}:a]aformat=channel_layouts=stereo,volume={GAIN[name]},adelay={max(0, round((start - TRIM) * 1000))}:all=1[f{n}]")
    effects.append(f"[f{n}]")
    n += 1

filters += [
    f"{''.join(voices)}amix=inputs={len(voices)}:normalize=0,apad,asplit=2[voice][key]",
    f"{''.join(effects)}amix=inputs={len(effects)}:normalize=0,apad[fx]",
    # The music sits low, and dips further while the voice speaks
    "[1:a]aresample=48000,aformat=channel_layouts=stereo,volume=0.32[bed]",
    "[bed][key]sidechaincompress=threshold=0.02:ratio=6:attack=20:release=400[ducked]",
    "[voice][fx][ducked]amix=inputs=3:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[mix]",
]
cmd += ["-filter_complex", ";".join(filters), "-map", "0:v", "-map", "[mix]",
        "-c:v", "libx264", "-preset", "slow", "-crf", "24", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-shortest", "-movflags", "+faststart", out]
subprocess.run(cmd, check=True)
