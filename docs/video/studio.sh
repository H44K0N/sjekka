#!/usr/bin/env bash
# Makes the four films on the about page (static/video/), all from code:
#
#   sjekka-promo-desktop   1920x1080, 60 fps: the "use our app" story
#   sjekka-promo-mobil     1080x1920, 60 fps: the same story, for a phone
#   sjekka-slik-fungerer   1920x1080: how the app works
#   sjekka-slik-bygget     1920x1080: how it was built, with AI
#
# What it does, in order:
#   1. capture.py   screenshots every page and state of the real app, run on
#                   demo data (demo_server.py), once at desktop size and once
#                   at phone size, with the boxes of what the films point at
#   2. flyers.py    pictures of this week's flyer offers, for the promo
#   3. music.py     downloads the three royalty free tracks, finds their beats
#   4. voice.py     records the voice-over: several takes of every line, the
#                   best kept (needs a GPU; lines already recorded are kept)
#   5. render.py    plays each film (films/*.html, a GSAP timeline) frame by
#                   frame in headless Chromium, on several cores
#   6. mix.py       sound design, music and voice mixed, encoded with the
#                   frames to H.264 with English captions (.vtt), and a poster
#
# Setup (once):
#   - the project's .venv (pip install -r requirements-dev.txt), Chromium
#     for Playwright (or CHROMIUM_PATH=/usr/bin/chromium), ffmpeg with libx264
#   - a second venv for the voice and the sound, with a CUDA GPU:
#       uv venv --python 3.12 "$STUDIO_PYTHON_VENV" and in it
#       uv pip install chatterbox-tts "setuptools<80" faster-whisper librosa scipy soundfile
#
#   STUDIO_DIR=~/.cache/sjekka-studio STUDIO_VENV=<that venv> docs/video/studio.sh [film ...]
#
# Films: promo-desktop promo-mobile how built (default: all four). Work
# files go in STUDIO_DIR, which is big (frames) and not in /tmp.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
studio="${STUDIO_DIR:-$HOME/.cache/sjekka-studio}"
venv="${STUDIO_VENV:?set STUDIO_VENV to the venv with chatterbox-tts (see the top of this file)}"
app_python="${APP_PYTHON:-$root/.venv/bin/python}"
films=("$@")
[ ${#films[@]} -gt 0 ] || films=(promo-desktop promo-mobile how built)
port=8790
server=""
trap '[ -n "$server" ] && kill "$server" 2>/dev/null; rm -f "$here/receipt.jpg"' EXIT
mkdir -p "$studio"/{shots,music,voice,frames,out,build/fonts,build/lib}

# Pieces the films load from the web, once: fonts for code, the animation library
build="$studio/build"
[ -f "$build/fonts/mono-400.woff2" ] || curl -sfL -o "$build/fonts/mono-400.woff2" "https://cdn.jsdelivr.net/npm/@fontsource/jetbrains-mono@5.0.20/files/jetbrains-mono-latin-400-normal.woff2"
[ -f "$build/fonts/mono-700.woff2" ] || curl -sfL -o "$build/fonts/mono-700.woff2" "https://cdn.jsdelivr.net/npm/@fontsource/jetbrains-mono@5.0.20/files/jetbrains-mono-latin-700-normal.woff2"
[ -f "$build/lib/gsap.min.js" ] || curl -sfL -o "$build/lib/gsap.min.js" "https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"
cp "$root"/static/fonts/Libron-*.woff2 "$build/fonts/"
for link in "films:$here/films" "shots:$studio/shots" "voice:$studio/voice" "music:$studio/music" "chains:$root/static/chains" "flyers:$studio/flyers"; do
    ln -sfn "${link#*:}" "$build/${link%%:*}"
done

"$app_python" "$here/make_receipt.py" "$here/receipt.jpg"

# 1. The real app, captured at both sizes. Each gets a fresh server, since
# the demo receipt can only be read once.
if curl -sf "http://127.0.0.1:$port/health" > /dev/null; then
    echo "Port $port is already in use: a demo server from an earlier run? Stop it first." >&2
    exit 1
fi
for format in desktop mobile; do
    rm -rf "$studio/demo-data" "$studio/shots/$format" && mkdir -p "$studio/demo-data"
    "$app_python" "$here/demo_server.py" "$studio/demo-data" "$port" > "$studio/demo.log" 2>&1 &
    server=$!
    for _ in $(seq 60); do curl -sf "http://127.0.0.1:$port/health" > /dev/null && break; sleep 0.5; done
    "$app_python" "$here/capture.py" "http://127.0.0.1:$port" "$format" "$studio/shots/$format" > /dev/null
    kill "$server"; wait "$server" 2>/dev/null || true; server=""
done
cp "$here/receipt.jpg" "$studio/shots/receipt.jpg"

# 2-4. Flyers, music, voice
"$app_python" "$here/flyers.py" "$studio/flyers"
"$venv/bin/python" "$here/music.py" "$studio/music" 2> /dev/null
"$venv/bin/python" "$here/voice.py" "$studio/voice" promo how built 2>&1 | grep -E "kept|Error" || true

# 5-6. Each film: frames, then sound and encode
poster() {  # poster <video> <seconds> <out.jpg>
    ffmpeg -v error -y -ss "$2" -i "$1" -frames:v 1 -vf "scale=1280:-2" -q:v 4 "$3"
}
for film in "${films[@]}"; do
    case "$film" in
        promo-desktop) name=promo; format=desktop; out=sjekka-promo-desktop; at=22.5; fps=60; crf=27 ;;
        promo-mobile)  name=promo; format=mobile;  out=sjekka-promo-mobil;   at=22.5; fps=60; crf=27 ;;
        how)           name=how;   format=desktop; out=sjekka-slik-fungerer; at=45; fps=30; crf=24 ;;
        built)         name=built; format=desktop; out=sjekka-slik-bygget;   at=12; fps=30; crf=24 ;;
        *) echo "unknown film: $film" >&2; exit 1 ;;
    esac
    frames="$studio/frames/$film"
    rm -rf "$frames"
    "$app_python" "$here/render.py" "$build" "$name" "$format" "$frames" --fps 60 --scale 1.5 --workers "${WORKERS:-8}" | tail -1
    "$venv/bin/python" "$here/mix.py" "$frames" "$studio/voice" "$studio/music" "$root/static/video/$out.mp4" --fps 60 --out-fps "$fps" --crf "$crf"
    poster "$root/static/video/$out.mp4" "$at" "$root/static/video/$out.jpg"
done
