#!/usr/bin/env bash
# Records the promo films shown on the about page (static/om.html), a short
# story with an English voice-over, music ("Motivating Mornings" by Ahjay
# Stelino, from Mixkit, downloaded here) and a few quiet sound effects:
#   static/video/sjekka-film-desktop.mp4   1920x1080, a mouse pointer
#   static/video/sjekka-film-mobil.mp4   1080x1920, a round touch dot
#
#   VOICE_MODELS=<dir> TTS_PYTHON=<venv python> docs/video/promo.sh
#
# The voice is Kokoro, set up as in make.sh; that venv's numpy and soundfile
# also make the music and effects. CHROMIUM_PATH=/usr/bin/chromium uses an
# installed Chromium. ONLY=desktop or ONLY=mobile makes just one. Rename the
# files (and the links in om.html) when they change, since browsers and
# Cloudflare keep them for a while.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
out="$here/../../static/video"
work="$(mktemp -d)"
port=8778
server=""
tts="${TTS_PYTHON:-python}"
trap '[ -n "$server" ] && kill "$server" 2>/dev/null; rm -rf "$work" "$here/raw" "$here/receipt.jpg"' EXIT
mkdir -p "$out"

python "$here/make_receipt.py" "$here/receipt.jpg"
"$tts" "$here/tts.py" "$VOICE_MODELS" "$work/voice" promo_script
"$tts" "$here/sfx.py" "$work/sfx" > /dev/null
export VOICE_DIR="$work/voice"

# The music: royalty free under the Mixkit Stock Music Free License, which
# asks for no credit. Its drums come in at 12.1 s, which music.py lines up
# with "Meet Sjekka".
curl -sfL -o "$work/track.mp3" "https://assets.mixkit.co/music/33/33.mp3"
ffmpeg -v error -i "$work/track.mp3" "$work/track.wav"

# Each film gets a fresh server: the demo receipt can only be read once
promo() {
    local mode="$1" name="$2"
    rm -rf "$work/data" "$here/raw" && mkdir -p "$work/data"
    python "$here/demo_server.py" "$work/data" "$port" > "$work/server.log" 2>&1 &
    server=$!
    for _ in $(seq 50); do curl -sf "http://127.0.0.1:$port/health" > /dev/null && break; sleep 0.2; done
    raw="$(python "$here/promo.py" "http://127.0.0.1:$port" "$mode" | tail -n 1)"
    kill "$server"; wait "$server" 2>/dev/null || true; server=""

    log="$here/raw/promo-$mode.json"
    trim=0.4  # promo_mix.py cuts this much blank video from the start
    length="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$raw")"
    seconds="$(python -c "print(round($length - $trim, 2))")"
    reveal="$(python -c "import json; print(round(json.load(open('$log'))['cues']['reveal'] - $trim, 2))")"
    "$tts" "$here/music.py" "$seconds" "$reveal" "$work/music.wav" "$work/track.wav" 12.1 > /dev/null
    python "$here/promo_mix.py" "$raw" "$log" "$VOICE_DIR" "$work/sfx" "$work/music.wav" "$out/$name.mp4"
    # The opening card as the still shown before playing
    ffmpeg -v error -y -ss 1.5 -i "$out/$name.mp4" -frames:v 1 -vf "scale=iw/2:-1" -q:v 4 "$out/$name.jpg"
    echo "Wrote $out/$name.mp4"
}
if [ "${ONLY:-}" != mobile ]; then promo desktop sjekka-film-desktop; fi
if [ "${ONLY:-}" != desktop ]; then promo mobile sjekka-film-mobil; fi
