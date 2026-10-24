#!/usr/bin/env bash
# Records two videos of the real app and worker on demo data (Gemini replaced
# by a fixed answer), driven by Playwright, with an optional voice-over:
#   docs/video/sjekka-showcase.mp4   desktop, 1920x1080, with the slides
#   docs/video/sjekka-mobile.mp4     phone, 1080x1920, the features only
#
#   pip install -r requirements-dev.txt && playwright install chromium ffmpeg
#   docs/video/make.sh                      # silent, captions only
#
# Voice-over (Kokoro, an open-source neural TTS model that runs locally):
#   python -m venv /tmp/tts && /tmp/tts/bin/pip install kokoro-onnx soundfile
#   download kokoro-v1.0.onnx and voices-v1.0.bin from
#   https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0
#   VOICE_MODELS=<that dir> TTS_PYTHON=/tmp/tts/bin/python docs/video/make.sh
#
# CHROMIUM_PATH=/usr/bin/chromium uses an installed Chromium. Needs ffmpeg
# with libx264 for the final encode.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)"
port=8777
server=""
trap '[ -n "$server" ] && kill "$server" 2>/dev/null; rm -rf "$work"' EXIT

voice="-"
if [ -n "${VOICE_MODELS:-}" ]; then
    voice="$work/voice"
    "${TTS_PYTHON:-python}" "$here/tts.py" "$VOICE_MODELS" "$voice"
fi

python "$here/make_receipt.py" "$here/receipt.jpg"
if [ "$voice" != "-" ]; then export VOICE_DIR="$voice"; fi

# Each video gets a fresh server, so both start from the same demo data
record() {
    local mode="$1" out="$2"
    rm -rf "$work/data" && mkdir -p "$work/data"
    python "$here/demo_server.py" "$work/data" "$port" > "$work/server.log" 2>&1 &
    server=$!
    for _ in $(seq 50); do curl -sf "http://127.0.0.1:$port/health" > /dev/null && break; sleep 0.2; done
    raw="$(python "$here/record.py" "http://127.0.0.1:$port" "$mode" | tail -n 1)"
    kill "$server"; wait "$server" 2>/dev/null || true; server=""
    python "$here/mix.py" "$raw" "$here/raw/cues-$mode.json" "$voice" "$here/$out"
    echo "Wrote $here/$out"
}
record desktop sjekka-showcase.mp4
record mobile sjekka-mobile.mp4
rm -rf "$here/raw" "$here/receipt.jpg"
