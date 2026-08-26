#!/usr/bin/env bash
# Publish lecture-note links from the Naropa Archive to the dashboard.
#
# The archive project (~/Documents/Naropa Archive) generates notes and emits a
# map of canvas_id → private Drive URL. It deliberately never writes to this
# repo — that separation is why a failure there cannot break Jennifer's board.
# This script is the one sanctioned bridge. Run it whenever new notes exist.
#
#   ./publish-video-notes.sh
#
set -euo pipefail

SRC="${1:-$HOME/Documents/Naropa Archive/dashboard-video-notes.json}"
DEST_DIR="$(cd "$(dirname "$0")" && pwd)/dashboard"
DEST="$DEST_DIR/video-notes.json"

[ -f "$SRC" ] || { echo "❌ No file at: $SRC"; exit 1; }

# Validate before publishing — a malformed file is handled gracefully by the
# board, but there is no reason to ship one.
COUNT=$(python3 - "$SRC" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
notes = d.get("notes", d) if isinstance(d, dict) else {}
assert isinstance(notes, dict), "expected an object of canvas_id → url"
bad = [k for k,v in notes.items()
       if not str((v or {}).get("url") if isinstance(v,dict) else v).startswith("https://")]
assert not bad, f"non-https or missing url for: {bad[:3]}"
print(len(notes))
PY
) || { echo "❌ Validation failed — not published."; exit 1; }

if [ "$COUNT" = "0" ]; then
  echo "ℹ 0 entries — nothing to publish yet. (Notes not generated, or no Drive URLs.)"
  exit 0
fi

cp "$SRC" "$DEST"
git add "$DEST"
if git diff --cached --quiet; then echo "ℹ No change since last publish."; exit 0; fi
git commit -qm "Publish $COUNT lecture-note link(s) from the archive"
git pull --rebase -q origin main
git push -q
echo "✓ Published $COUNT note link(s). Live in ~1 min at:"
echo "  https://jtbdashboard.fitzsimmons.org/dashboard/"
echo "  (Jennifer may need one hard refresh.)"
