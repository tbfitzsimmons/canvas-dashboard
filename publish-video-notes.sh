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

# Link health check. Learned 2026-08-27: an id can be well-formed and still not
# exist. Unauthenticated, Drive answers 401 for a real private file and 404 for
# one that is not there, so existence is checkable without credentials.
# This matters most right after the archive re-uploads: converting a .md into a
# Google Doc mints a NEW file id, silently invalidating every published link.
# Refuse to publish rather than ship 404s to her board.
# Set SKIP_LINK_CHECK=1 to bypass (offline only).
if [ "${SKIP_LINK_CHECK:-0}" != "1" ]; then
  echo "Checking $COUNT link(s) against Drive..."
  IDS=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); n=d.get("notes",d); [print(k, (v.get("url") if isinstance(v,dict) else v).split("/d/")[1].split("/")[0]) for k,v in n.items()]' "$SRC")
  BAD=0
  while read -r key id; do
    [ -z "$id" ] && continue
    code=$(curl -s -o /dev/null -m 15 -w "%{http_code}" -A "Mozilla/5.0" "https://drive.google.com/file/d/$id/view" || echo "000")
    case "$code" in
      401|302) printf "  OK   %-30s exists (login required)\n" "$key" ;;
      404)     printf "  FAIL %-30s NO SUCH FILE (%s)\n" "$key" "$id"; BAD=$((BAD+1)) ;;
      000)     printf "  ??   %-30s network unreachable, cannot verify\n" "$key"; BAD=$((BAD+1)) ;;
      *)       printf "  ??   %-30s unexpected HTTP %s\n" "$key" "$code"; BAD=$((BAD+1)) ;;
    esac
  done <<< "$IDS"
  if [ "$BAD" -ne 0 ]; then
    echo "❌ $BAD link(s) failed the existence check — NOT published."
    echo "   If the archive just converted notes to Google Docs, the ids changed."
    echo "   Ask it to regenerate the file, then re-run this."
    exit 1
  fi
  echo "✓ all $COUNT link(s) resolve"
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
