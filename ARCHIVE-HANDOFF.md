# Open items for the archive sessions

Written by the dashboard session. Coordination happens through files, not
messages — no session here outlives its conversation. Read this when you start.

Last updated: 2026-09-11

---

## 1. ⚠️ `prior_notes.json` undercounts written notes by 32×

**200 prior-term reading notes exist on disk. `prior_notes.json` marks 6 as
`written: true`.**

```
records with a notes_path          587
  ...file EXISTS on disk           200
  ...marked written == true          6
  ⚠ exists on disk, written=false  194
  written=true but file missing      0
```

A further **70** `notes.md` files under `Naropa Archive Content/` (excluding
Fall 2026) are not referenced by `prior_notes.json` at all — 270 prior-term
notes on disk against 587 tracked records.

This is the state-vs-disk failure your own `validate.py` rule 1 exists to
catch: *compare state against DISK, never state against itself.* Rule 1 is not
currently applied to `prior_notes.json`.

**Why it matters to the board.** If the reading-notes contract is emitted from
`written == true`, it ships 19 links. Built from what is actually on disk it
ships **226** — and all 226 notes are already uploaded to Drive, so nothing
else has to happen first.

| board | file-addressable items | from `written` | from disk | in Drive |
|---|---|---|---|---|
| Spring 2025 | 168 | 7 | **57** | 57 |
| Summer 2025 | 226 | 0 | **40** | 40 |
| Fall 2025 | 150 | 5 | **38** | 38 |
| Spring 2026 | 170 | 6 | **37** | 37 |
| Summer 2026 | 206 | 1 | **54** | 54 |
| Fall 2026 | 48 | 0 | 0 | 0 |
| **TOTAL** | **968** | **19** | **226** | **226** |

Please reconcile the flag against disk before emitting the contract, or emit
from disk directly. Do not delete the 194 notes to make state match — the files
are the truth here, the flag is wrong.

## 2. The reading-notes contract the board is waiting for

Everything on the dashboard side is built, deployed and tested. It renders
nothing until this file exists, so there is no risk in emitting it early or
incrementally.

```
path:  ~/Documents/Naropa Archive/dashboard-reading-notes.json
shape: { "notes": { "file:<canvas_course_id>:<canvas_file_id>": { "url": "https://…" } } }
```

`prior_notes.json` already carries everything needed: `primary` is
`"<course>:<file>"`, so the key is `"file:" + primary`. Emit one entry per
`copies` member too — dedup means one note legitimately serves several courses,
and that is where most of the 226 comes from.

Publish with `./publish-reading-notes.sh` in the dashboard repo. It validates
key shape, refuses a contract smaller than the published one, checks every
Drive link resolves (401 real / 404 missing), and reports how many keys reach
an item on a board.

Send **path + entry count + sha256**. Never contents, never ids.

## 3. Still outstanding, lower urgency

- **`discussions.json`: 155 done, 21 `gap`.** The 21 are unexplained here;
  name them rather than letting a reader infer they were skipped.
- **`submissions.json` (204) and `assignment_pages.json` (236) have no status
  field at all** — every record is `null`. Cannot tell captured from pending
  from failed. These are the highest-value artifacts in the archive (her own
  written work, with feedback) and currently have the weakest tracking.
- **`documents-prior.json`: 12 `ocr_unusable`, 37 `skipped`.** Both counts are
  fine as outcomes; they just need to be distinguishable from "not attempted".

## 4. Standing constraints

- **Title and link only on the board.** Ruled by Brooks 2026-08-28. Archived
  boards carry zero body text; reading notes render a link into authenticated
  Drive and never note text or extracted source text. Copyright, not FERPA, is
  the live issue for readings.
- **No transcript or summary for recorded class discussion.** Four artifacts
  removed under this; verified absent.
- **Canvas access ends after graduation (~May 2027).** `canvas_target` is
  captured on 679/682 module-item rows so archived boards can be re-pointed at
  Drive offline. Re-pointing tops out at 76% today; assignment-page and
  discussion HTML snapshots would take it to ~94%.
