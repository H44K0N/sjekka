#!/usr/bin/env bash
# Nightly backup of the Sjekka database, run by sjekka-backup.timer (installed
# by deploy/setup-vps.sh). Uses SQLite's online backup API, so it's safe while
# the app is writing, checks the copy, and keeps the last $KEEP_DAYS days in
# /opt/sjekka/backups.
#
# The backups are on the same disk as the database. Copy them somewhere else
# too, e.g. scp from another machine.
set -euo pipefail

dir="${SJEKKA_DIR:-/opt/sjekka}"
dest="$dir/backups"
keep_days="${KEEP_DAYS:-14}"
db="$dir/data/sjekka.db"
out="$dest/sjekka-$(date +%F).db"

[ -f "$db" ] || { echo "No database at $db yet" >&2; exit 0; }
install -d -m 700 "$dest"

python3 - "$db" "$out" <<'PY'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
result = dst.execute("PRAGMA integrity_check").fetchone()[0]
src.close()
dst.close()
if result != "ok":
    sys.exit(f"Integrity check failed: {result}")
PY

gzip -f "$out"
find "$dest" -name 'sjekka-*.db.gz' -mtime +"$keep_days" -delete
echo "Backed up to $out.gz"
