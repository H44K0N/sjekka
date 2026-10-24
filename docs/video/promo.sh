#!/usr/bin/env bash
# Records the short promo videos shown on the about page (static/om.html):
#   static/video/sjekka-desktop.mp4   1920x1080, a mouse pointer
#   static/video/sjekka-mobil.mp4     1080x1920, a round touch dot
# with a quiet background track made by music.py.
#
#   MUSIC_PYTHON=<python with numpy and soundfile> docs/video/promo.sh
#
# CHROMIUM_PATH=/usr/bin/chromium uses an installed Chromium. Needs ffmpeg
# with libx264. Rename the files (and the links in om.html) when they
# change, since browsers and Cloudflare keep them for a while.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
out="$here/../../static/video"
work="$(mktemp -d)"
port=8778
server=""
trap '[ -n "$server" ] && kill "$server" 2>/dev/null; rm -rf "$work" "$here/raw" "$here/receipt.jpg"' EXIT
mkdir -p "$out"
python "$here/make_receipt.py" "$here/receipt.jpg"

# Each video gets a fresh server: the demo receipt can only be read once
promo() {
    local mode="$1" name="$2"
    rm -rf "$work/data" "$here/raw" && mkdir -p "$work/data"
    python "$here/demo_server.py" "$work/data" "$port" > "$work/server.log" 2>&1 &
    server=$!
    for _ in $(seq 50); do curl -sf "http://127.0.0.1:$port/health" > /dev/null && break; sleep 0.2; done
    raw="$(python "$here/promo.py" "http://127.0.0.1:$port" "$mode" | tail -n 1)"
    kill "$server"; wait "$server" 2>/dev/null || true; server=""

    trim=0.4  # blank frames at the very start
    length="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$raw")"
    seconds="$(python -c "print(round($length - $trim, 2))")"
    "${MUSIC_PYTHON:-python}" "$here/music.py" "$seconds" "$work/music.wav" > /dev/null
    # Quiet on purpose: background, not a soundtrack
    ffmpeg -v error -y -ss "$trim" -i "$raw" -i "$work/music.wav" \
        -map 0:v -map 1:a -c:v libx264 -preset slow -crf 24 -pix_fmt yuv420p -r 30 \
        -af "loudnorm=I=-24:TP=-3:LRA=7" -c:a aac -b:a 128k -ar 48000 -shortest \
        -movflags +faststart "$out/$name.mp4"
    # The title card ("Hvor er handlelisten billigst?") as the still shown before playing
    ffmpeg -v error -y -ss 2.5 -i "$out/$name.mp4" -frames:v 1 -vf "scale=iw/2:-1" -q:v 4 "$out/$name.jpg"
    echo "Wrote $out/$name.mp4"
}
# ONLY=desktop or ONLY=mobile makes just one
if [ "${ONLY:-}" != mobile ]; then promo desktop sjekka-desktop; fi
if [ "${ONLY:-}" != desktop ]; then promo mobile sjekka-mobil; fi
