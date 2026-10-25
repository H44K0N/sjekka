"""Records the films' voice-over like a session with a voice actor: several
takes of every line, and the best one kept.

    python voice.py <out dir> [film ...]

The voice is Chatterbox (Resemble AI, MIT licence), an open-source
text-to-speech model run locally on the GPU; it's livelier than Kokoro, but
no two takes are alike and now and then one mumbles or swallows a word. So
each line is spoken TAKES times and every take is judged by ear, as far as
code can: transcribed back with Whisper and compared with the script, with
its pace and pauses checked and its pitch kept close to the rest of the
session. The best take is trimmed and kept as <film>/<cue>.wav.

The first line recorded becomes the reference the model copies its voice
from, so every line in every film has the same voice. timing.json has each
line's length and when each word is spoken (from Whisper), which the films
use to time scenes and to set words on screen as they're said.

Needs a CUDA GPU, and in a venv: chatterbox-tts, "setuptools<80" (its
watermarker imports pkg_resources), faster-whisper and librosa. Every take
carries Chatterbox's inaudible watermark, which marks the voice as AI."""
import json
import re
import sys
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf
import torch
import torchaudio
from chatterbox.tts import ChatterboxTTS
from faster_whisper import WhisperModel

HERE = Path(__file__).parent
OUT = Path(sys.argv[1])
LINES = json.loads((HERE / "films" / "lines.json").read_text())
FILMS = sys.argv[2:] or list(LINES)
TAKES = 5
# An English voice reads these wrong as written
PRONOUNCE = {"Sjekka": "Shecka", "Haakon": "Hawkon", "Rema": "Remma", "Grandiosa": "Grandi-osa"}
# How the voice reads each film: the promo with more drama, the others plainer
STYLE = {"promo": dict(exaggeration=0.6, cfg_weight=0.4), "how": dict(exaggeration=0.5, cfg_weight=0.5),
         "built": dict(exaggeration=0.5, cfg_weight=0.5)}
# What Whisper may hear for a respelled word
HEARD = {"sjekka": {"shecka", "shekka", "sheka", "shekha", "checka", "chekka", "sjekka", "shaker", "check"},
         "haakon": {"hawkon", "hakon", "haakon", "hawken", "hocon", "hawkins"},
         "haakon's": {"hawkon's", "hakon's", "haakon's", "hawkins"},
         "grandiosa": {"grandiosa", "grandi", "grandioso"}}
NUMBERS = {w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                            "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}


def spoken(text: str) -> str:
    for word, sound in PRONOUNCE.items():
        text = re.sub(rf"\b{word}\b", sound, text)
    return text


def words(text: str) -> list[str]:
    """The words, with numbers as digits whichever way they're written:
    Whisper hears "fifty-two" as "52"."""
    out = []
    for w in re.findall(r"[a-z0-9']+", text.lower().replace("-", " ")):
        if w in TENS:
            out.append(str(TENS[w]))
        elif w in NUMBERS and out and out[-1].isdigit() and out[-1].endswith("0") and len(out[-1]) == 2 and w != "zero":
            out[-1] = str(int(out[-1]) + int(NUMBERS[w]))
        elif w == "hundred" and out and out[-1].isdigit():
            out[-1] = str(int(out[-1]) * 100)
        else:
            out.append(NUMBERS.get(w, w))
    return out


def errors(said: list[str], heard: list[str]) -> int:
    """Word-level edit distance, forgiving the respelled words."""
    same = lambda a, b: a == b or b in HEARD.get(a, ())  # noqa: E731
    d = list(range(len(heard) + 1))
    for i, a in enumerate(said, 1):
        prev, d[0] = d[0], i
        for j, b in enumerate(heard, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (not same(a, b)))
    return d[-1]


def trim(wav: np.ndarray, rate: int) -> np.ndarray:
    """Without the silence around the line, but with a breath of room."""
    _, (start, end) = librosa.effects.trim(wav, top_db=38)
    pad = int(0.04 * rate)
    wav = wav[max(0, start - pad): end + pad].copy()
    fade = int(0.01 * rate)
    wav[:fade] *= np.linspace(0, 1, fade)
    wav[-fade:] *= np.linspace(1, 0, fade)
    return wav


def judge(wav, rate, text, whisper, pitch_ref):
    """A take's score (lower is better) and its words with times."""
    audio16 = librosa.resample(wav, orig_sr=rate, target_sr=16000)
    segments, _ = whisper.transcribe(audio16, word_timestamps=True, language="en")
    heard = [w for s in segments for w in s.words]
    wrong = errors(words(text), words(" ".join(w.word for w in heard)))
    seconds = len(wav) / rate
    pace = len(words(text)) / seconds  # words a second; natural reading is 2.3-3.3
    # Long gaps between words sound like a stumble
    gaps = [b.start - a.end for a, b in zip(heard, heard[1:], strict=False)]
    stumble = sum(max(0.0, g - 0.6) for g in gaps)
    f0, voiced, _ = librosa.pyin(audio16, fmin=70, fmax=400, sr=16000)
    pitch = float(np.nanmedian(f0[voiced])) if voiced.any() else 0.0
    drift = abs(12 * np.log2(pitch / pitch_ref)) if pitch_ref and pitch else 0.0
    score = 4 * wrong + 3 * max(0.0, 2.3 - pace) + 3 * max(0.0, pace - 3.4) + 2 * stumble + 0.5 * max(0.0, drift - 1.5)
    timed = [{"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2)} for w in heard]
    return score, wrong, pace, pitch, timed


def main():
    model = ChatterboxTTS.from_pretrained(device="cuda")
    whisper = WhisperModel("small.en", device="cpu", compute_type="int8")
    reference = OUT / "reference.wav"
    pitch_ref = None
    if reference.exists():
        pitch_ref = json.loads((OUT / "reference.json").read_text())["pitch"]

    for film in FILMS:
        (OUT / film).mkdir(parents=True, exist_ok=True)
        timing_file = OUT / film / "timing.json"
        timing = json.loads(timing_file.read_text()) if timing_file.exists() else {}
        for cue, text in LINES[film].items():
            # Kept from a run before, unless the line changed
            if timing.get(cue, {}).get("text") == text and (OUT / film / f"{cue}.wav").exists():
                continue
            best = None
            for take in range(TAKES):
                torch.manual_seed(1000 * take + len(text))
                prompt = {"audio_prompt_path": str(reference)} if reference.exists() else {}
                raw = model.generate(spoken(text), **STYLE[film], **prompt).squeeze(0).cpu().numpy()
                wav = trim(raw, model.sr)
                score, wrong, pace, pitch, timed = judge(wav, model.sr, text, whisper, pitch_ref)
                print(f"  {film}/{cue} take {take + 1}: score {score:4.1f}  {wrong} wrong  {pace:.1f} w/s  {pitch:.0f} Hz")
                if best is None or score < best[0]:
                    best = (score, wav, timed, wrong)
                if score < 0.5:
                    break
            score, wav, timed, wrong = best
            sf.write(OUT / film / f"{cue}.wav", wav, model.sr)
            timing[cue] = {"text": text, "seconds": round(len(wav) / model.sr, 2), "words": timed, "score": round(score, 1)}
            print(f"{film}/{cue}: kept, {timing[cue]['seconds']} s, {wrong} words off")
            timing_file.write_text(json.dumps(timing, indent=1, ensure_ascii=False))
            # The session's first good line sets the voice for all the rest
            if not reference.exists():
                torchaudio.save(str(reference), torch.from_numpy(wav).unsqueeze(0), model.sr)
                _, _, _, pitch_ref, _ = judge(wav, model.sr, text, whisper, None)
                (OUT / "reference.json").write_text(json.dumps({"pitch": pitch_ref, "from": f"{film}/{cue}"}))


main()
