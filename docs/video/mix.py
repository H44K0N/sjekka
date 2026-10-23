"""Encodes the recording and lays each narration line at the moment record.py
started it.

    python mix.py <raw video> <cues.json> <voice dir or "-"> <out.mp4>

With "-" for the voice dir the video is encoded silent."""
import json
import subprocess
import sys

video, cues_file, voice, out = sys.argv[1:5]
TRIM = 0.4  # blank frames at the very start of the recording

cmd = ["ffmpeg", "-v", "error", "-y", "-ss", str(TRIM), "-i", video]
video_args = ["-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p"]

if voice == "-":
    cmd += ["-map", "0:v", *video_args, "-an"]
else:
    cues = json.loads(open(cues_file).read())
    filters = []
    for i, (cue, start) in enumerate(cues.items(), start=1):
        cmd += ["-i", f"{voice}/{cue}.wav"]
        delay = max(0, round((start - TRIM) * 1000))
        filters.append(f"[{i}:a]adelay={delay}:all=1[a{i}]")
    labels = "".join(f"[a{i}]" for i in range(1, len(cues) + 1))
    # Lines never overlap, so mixing without normalising keeps each at full
    # level; loudnorm then brings the whole track to a standard loudness, and
    # apad + -shortest make the audio exactly as long as the video.
    filters.append(f"{labels}amix=inputs={len(cues)}:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11,apad[voice]")
    cmd += ["-filter_complex", ";".join(filters), "-map", "0:v", "-map", "[voice]",
            *video_args, "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest"]

cmd += ["-movflags", "+faststart", out]
subprocess.run(cmd, check=True)
