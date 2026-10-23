#!/usr/bin/env bash
# Records docs/video/sjekka-showcase.mp4: the real app and worker on demo data
# (Gemini replaced by a fixed answer), driven by Playwright, plus the slides.
#
#   pip install -r requirements-dev.txt && playwright install chromium ffmpeg
#   docs/video/make.sh            # or CHROMIUM_PATH=/usr/bin/chromium docs/video/make.sh
#
# Needs ffmpeg with libx264 for the final encode.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)"
port=8777
server=""
trap '[ -n "$server" ] && kill "$server" 2>/dev/null; rm -rf "$work"' EXIT

python "$here/make_receipt.py" "$here/receipt.jpg"
mkdir -p "$work/data"
python "$here/demo_server.py" "$work/data" "$port" > "$work/server.log" 2>&1 &
server=$!
for _ in $(seq 50); do curl -sf "http://127.0.0.1:$port/health" > /dev/null && break; sleep 0.2; done

raw="$(python "$here/record.py" "http://127.0.0.1:$port" | tail -n 1)"
# Skip the blank first frames
ffmpeg -v error -y -ss 0.4 -i "$raw" -c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p \
    -movflags +faststart -an "$here/sjekka-showcase.mp4"
rm -rf "$here/raw" "$here/receipt.jpg"
echo "Wrote $here/sjekka-showcase.mp4"
