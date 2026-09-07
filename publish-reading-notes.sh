#!/usr/bin/env bash
# Publish READING-note links from the Naropa Archive to the dashboard.
#
# Sibling of publish-video-notes.sh, deliberately a separate file and a separate
# contract: two producers writing one file is a failure we already had. The two
# scripts share their guard logic by copy, not by import — if you change a guard
# here, grep for it in publish-video-notes.sh and change it there too.
#
# Contract shape, agreed with the archive project 2026-08-28:
#   { "notes": { "file:<canvas_course_id>:<canvas_file_id>": { "url": "https://…" } } }
# Keyed the same way as Item.canvas_target and the archive's manifest.json, so
# neither side needs a lookup table.
#
#   ./publish-reading-notes.sh
#
set -euo pipefail

SRC="${1:-$HOME/Documents/Naropa Archive/dashboard-reading-notes.json}"
DEST_DIR="$(cd "$(dirname "$0")" && pwd)/dashboard"
DEST="$DEST_DIR/reading-notes.json"

[ -f "$SRC" ] || { echo "ℹ No file at: $SRC"; echo "  The archive has not emitted reading notes yet. Nothing to do."; exit 0; }

COUNT=$(python3 - "$SRC" <<'PY'
import json, sys, re
d = json.load(open(sys.argv[1]))
notes = d.get("notes", d) if isinstance(d, dict) else {}
assert isinstance(notes, dict), "expected an object of key → url"
bad = [k for k, v in notes.items()
       if not str((v or {}).get("url") if isinstance(v, dict) else v).startswith("https://")]
assert not bad, f"non-https or missing url for: {bad[:3]}"
# Key shape is load-bearing: the board resolves items to file:<course>:<file>.
# A wrong shape silently matches nothing and looks like "no notes yet".
KEY = re.compile(r"^file:\d+:\d+$")
wrong = [k for k in notes if not KEY.match(k)]
assert not wrong, f"keys must look like file:<course_id>:<file_id>; got: {wrong[:3]}"
print(len(notes))
PY
) || { echo "❌ Validation failed — not published."; exit 1; }

if [ "$COUNT" = "0" ]; then
  echo "ℹ 0 entries — nothing to publish yet."
  exit 0
fi

# Shrink guard — see publish-video-notes.sh for the incident that motivated it.
# A contract that loses entries still has every REMAINING link resolve, so the
# link check below passes cleanly while silently removing working links.
if [ -f "$DEST" ]; then
  PREV=$(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print(len(d.get("notes",d)))' "$DEST" 2>/dev/null || echo 0)
  if [ "$COUNT" -lt "$PREV" ] && [ "${ALLOW_SHRINK:-0}" != "1" ]; then
    echo "❌ REFUSING: incoming contract has $COUNT entries, published has $PREV."
    echo "   These keys would LOSE their links:"
    python3 - "$SRC" "$DEST" <<'PY'
import json,sys
new=json.load(open(sys.argv[1])); new=new.get("notes",new)
old=json.load(open(sys.argv[2])); old=old.get("notes",old)
for k in sorted(set(old)-set(new)): print(f"     {k}")
PY
    echo "   Nothing published. If the shrink is intended: ALLOW_SHRINK=1 $0"
    exit 1
  fi
fi

# Coverage report — how many of these keys actually reach an item on a board?
# A contract full of keys that match nothing publishes cleanly and shows zero
# links, which is indistinguishable from "not generated yet" unless we say so.
python3 - "$SRC" "$DEST_DIR" <<'PY'
import json, os, re, sys, glob
notes = json.load(open(sys.argv[1])); notes = notes.get("notes", notes)
FILE_RE = re.compile(r"/courses/(\d+)/files/(\d+)")
board_keys, boards = set(), []
for p in [os.path.join(sys.argv[2], "data.json")] + sorted(glob.glob(os.path.join(sys.argv[2], "archive", "*.json"))):
    if not os.path.exists(p) or p.endswith("index.json"): continue
    try: d = json.load(open(p))
    except Exception: continue
    n = 0
    for it in d.get("items", []):
        t = it.get("canvas_target") or ""
        if t.startswith("file:"): board_keys.add(t); n += 1; continue
        m = FILE_RE.search(it.get("link") or "")
        if m: board_keys.add(f"file:{m.group(1)}:{m.group(2)}"); n += 1
    boards.append((os.path.basename(p), n))
hit = set(notes) & board_keys
print(f"  {len(hit)} of {len(notes)} contract keys reach an item on a board")
if len(hit) < len(notes):
    print(f"  ⚠ {len(notes)-len(hit)} key(s) match no item — they will render nothing.")
    for k in sorted(set(notes) - board_keys)[:5]: print(f"      {k}")
PY

# Link health check — 401 = real private file, 404 = does not exist.
if [ "${SKIP_LINK_CHECK:-0}" != "1" ]; then
  echo "Checking $COUNT link(s) against Drive..."
  IDS=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); n=d.get("notes",d); [print(k, (v.get("url") if isinstance(v,dict) else v).split("/d/")[1].split("/")[0]) for k,v in n.items()]' "$SRC")
  BAD=0
  while read -r key id; do
    [ -z "$id" ] && continue
    code=$(curl -s -o /dev/null -m 15 -w "%{http_code}" -A "Mozilla/5.0" "https://drive.google.com/file/d/$id/view" || echo "000")
    case "$code" in
      401|302) : ;;
      404)     printf "  FAIL %-30s NO SUCH FILE (%s)\n" "$key" "$id"; BAD=$((BAD+1)) ;;
      000)     printf "  ??   %-30s network unreachable\n" "$key"; BAD=$((BAD+1)) ;;
      *)       printf "  ??   %-30s unexpected HTTP %s\n" "$key" "$code"; BAD=$((BAD+1)) ;;
    esac
  done <<< "$IDS"
  if [ "$BAD" -ne 0 ]; then
    echo "❌ $BAD link(s) failed the existence check — NOT published."
    exit 1
  fi
  echo "✓ all $COUNT link(s) resolve"
fi

cp "$SRC" "$DEST"
git add "$DEST"
if git diff --cached --quiet; then echo "ℹ No change since last publish."; exit 0; fi
git commit -qm "Publish $COUNT reading-note link(s) from the archive"
git pull --rebase -q origin main
git push -q
echo "✓ Published $COUNT reading-note link(s). Live in ~1 min."
