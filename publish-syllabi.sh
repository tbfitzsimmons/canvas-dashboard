#!/usr/bin/env bash
# Publish SYLLABUS links from the Naropa Archive to the dashboard.
#
# Third sibling of publish-video-notes.sh and publish-reading-notes.sh,
# deliberately a separate file and a separate contract: two producers writing one
# file is a failure this project already had. The three scripts share their guard
# logic BY COPY, not by import — if you change a guard here, grep for it in
# publish-video-notes.sh and publish-reading-notes.sh and change it there too.
#
# Contract shape:
#   { "syllabi": { "<canvas_course_id>": { "url": "https://…" } } }
#
# Keyed on the CANVAS COURSE ID (a bare integer, e.g. 7208), which is unique by
# construction and already sits on every course object in data.json /
# archive/*.json as `canvas_id` (measured 2026-11-05: 30 of 30 courses have one).
# NOT on course code. A code is a section label that recurs across terms —
# CNSM-651E-LA and -LB are different courses — so its uniqueness on today's six
# boards is incidental (also measured: 30 codes, 0 collisions), not guaranteed.
# The coverage report below exists so a key that matches nothing says so out
# loud instead of looking like "not generated yet".
#
# Why this exists: syllabi are what Jennifer named, unprompted, as what licensure
# requires, and they die with her Canvas access (~May 2027). They are also the
# hardest to get — Naropa's real syllabi live in Simple Syllabus, an external LTI
# tool no Canvas API reaches (BACKLOG item 1), so they arrive by hand, a few at a
# time. This script is the ONE sanctioned bridge, and it reads from the FILE ON
# DISK, never from a message (fact 17: three fabricated Drive ids once arrived in
# a "FULL FILE CONTENTS" paste). Keep it that way.
#
#   ./publish-syllabi.sh [path-to-contract]
#
# Env overrides:  ALLOW_SHRINK=1   publish a contract with fewer entries
#                 SKIP_LINK_CHECK=1 skip the Drive existence probe (offline)
#
set -euo pipefail

SRC="${1:-$HOME/Documents/Naropa Archive/dashboard-syllabi.json}"
DEST_DIR="$(cd "$(dirname "$0")" && pwd)/dashboard"
DEST="$DEST_DIR/syllabi.json"

[ -f "$SRC" ] || { echo "ℹ No file at: $SRC"; echo "  The archive has not emitted syllabus links yet. Nothing to do."; exit 0; }

COUNT=$(python3 - "$SRC" <<'PY'
import json, sys, re
d = json.load(open(sys.argv[1]))
syl = d.get("syllabi", d) if isinstance(d, dict) else {}
assert isinstance(syl, dict), "expected an object of canvas_course_id → url"
bad = [k for k, v in syl.items()
       if not str((v or {}).get("url") if isinstance(v, dict) else v).startswith("https://")]
assert not bad, f"non-https or missing url for: {bad[:3]}"
# Key shape is load-bearing: the board resolves course.canvas_id → this key as a
# string. A course CODE here silently matches nothing and looks like "no syllabi
# yet"; worse, a code is ambiguous across terms and sections.
KEY = re.compile(r"^\d+$")
wrong = [k for k in syl if not KEY.match(str(k))]
assert not wrong, f"keys must be a bare integer canvas course id; got: {wrong[:3]}"
print(len(syl))
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
  PREV=$(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print(len(d.get("syllabi",d)))' "$DEST" 2>/dev/null || echo 0)
  if [ "$COUNT" -lt "$PREV" ] && [ "${ALLOW_SHRINK:-0}" != "1" ]; then
    echo "❌ REFUSING: incoming contract has $COUNT entries, published has $PREV."
    echo "   These course ids would LOSE their syllabus links:"
    python3 - "$SRC" "$DEST" <<'PY'
import json,sys
new=json.load(open(sys.argv[1])); new=new.get("syllabi",new)
old=json.load(open(sys.argv[2])); old=old.get("syllabi",old)
for k in sorted(set(old)-set(new), key=lambda x: str(x)): print(f"     {k}")
PY
    echo "   Nothing published. If the shrink is intended: ALLOW_SHRINK=1 $0"
    exit 1
  fi
fi

# Coverage report — how many of these keys actually reach a COURSE on a board?
# A contract full of keys that match nothing publishes cleanly and shows zero
# links, which is indistinguishable from "not generated yet" unless we say so.
# The join is course.canvas_id (int) → contract key (string), across the live
# board AND every archived semester, because a syllabus link renders on both.
python3 - "$SRC" "$DEST_DIR" <<'PY'
import json, os, sys, glob
syl = json.load(open(sys.argv[1])); syl = syl.get("syllabi", syl)
course_ids, boards = {}, []
for p in [os.path.join(sys.argv[2], "data.json")] + sorted(glob.glob(os.path.join(sys.argv[2], "archive", "*.json"))):
    if not os.path.exists(p) or os.path.basename(p) == "index.json": continue
    try: d = json.load(open(p))
    except Exception: continue
    n = 0
    for c in d.get("courses", []):
        cid = c.get("canvas_id")
        if cid is None: continue
        course_ids[str(cid)] = c.get("code") or c.get("name") or "?"
        n += 1
    boards.append((os.path.basename(p), n))
hit = sorted(set(map(str, syl)) & set(course_ids))
print(f"  {len(hit)} of {len(syl)} contract keys match a course on a board "
      f"({len(course_ids)} courses across {len(boards)} board(s))")
for k in hit: print(f"      ✓ {k}  {course_ids[k]}")
miss = sorted(set(map(str, syl)) - set(course_ids))
if miss:
    print(f"  ⚠ {len(miss)} key(s) match no course — they will render nothing.")
    for k in miss[:5]: print(f"      {k}")
PY

# Link health check — the adjudication method from fact 17. Unauthenticated,
# Drive answers 401 for a file that EXISTS but needs a login (correct for her
# private syllabi) and 404 for one that does not exist, so existence is settled
# without authenticating. A bogus control id 404s, which is what makes the check
# able to fail. Any 404 refuses the WHOLE batch.
# A URL that is not Drive-shaped cannot be adjudicated that way, so it is probed
# directly instead — a control has to run on every path the input can arrive by
# (fact 18), not just the common one.
if [ "${SKIP_LINK_CHECK:-0}" != "1" ]; then
  echo "Checking $COUNT link(s)..."
  IDS=$(python3 - "$SRC" <<'PY'
import json, re, sys
d = json.load(open(sys.argv[1])); syl = d.get("syllabi", d)
for k, v in syl.items():
    url = v.get("url") if isinstance(v, dict) else v
    m = re.search(r"/d/([\w-]+)", url or "")
    print(k, ("drive " + m.group(1)) if m else ("direct " + url))
PY
)
  BAD=0
  while read -r key kind target; do
    [ -z "${target:-}" ] && continue
    if [ "$kind" = "drive" ]; then
      probe="https://drive.google.com/file/d/$target/view"
    else
      probe="$target"
    fi
    code=$(curl -s -o /dev/null -m 15 -w "%{http_code}" -A "Mozilla/5.0" "$probe" || echo "000")
    case "$code" in
      200|401|302) : ;;
      404)     printf "  FAIL %-12s NO SUCH FILE (%s)\n" "$key" "$target"; BAD=$((BAD+1)) ;;
      000)     printf "  ??   %-12s network unreachable\n" "$key"; BAD=$((BAD+1)) ;;
      *)       printf "  ??   %-12s unexpected HTTP %s\n" "$key" "$code"; BAD=$((BAD+1)) ;;
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
git commit -qm "Publish $COUNT syllabus link(s) from the archive"
git pull --rebase -q origin main
git push -q
echo "✓ Published $COUNT syllabus link(s). Live in ~1 min."
