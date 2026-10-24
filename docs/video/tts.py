"""Renders each narration cue to a WAV with Kokoro and writes their lengths
to durations.json, which record.py uses to time the scenes.

    python tts.py <model dir with kokoro-v1.0.onnx and voices-v1.0.bin> <out dir>

Needs `pip install kokoro-onnx soundfile` (see make.sh)."""
import json
import sys
from pathlib import Path

import soundfile as sf
from kokoro_onnx import Kokoro

sys.path.insert(0, str(Path(__file__).parent))
from narration import CUES, MOBILE, SPEED, VOICE, spoken  # noqa: E402

models, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
kokoro = Kokoro(str(models / "kokoro-v1.0.onnx"), str(models / "voices-v1.0.bin"))

durations = {}
for cue, text in {**CUES, **MOBILE}.items():
    samples, rate = kokoro.create(spoken(text), voice=VOICE, speed=SPEED, lang="en-us")
    sf.write(out / f"{cue}.wav", samples, rate)
    durations[cue] = round(len(samples) / rate, 2)
    print(f"{cue:10} {durations[cue]:5.1f}s")

(out / "durations.json").write_text(json.dumps(durations, indent=2))
print(f"total {sum(durations.values()):.1f}s of speech")
