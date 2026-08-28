# CLAUDE.md — Canvas Dashboard Project Context

This file is read automatically by Claude Code when the project is opened.
It contains everything needed to continue work without re-reading the full history.

---

## What this project is

A static semester dashboard for **Jennifer** (grad student, Naropa University).
Pulls every assignment, quiz, discussion, reading, video, and exam from Canvas
via the REST API, classifies them, and presents a clean weekly view at:

**https://jtbdashboard.fitzsimmons.org/dashboard/**

Auto-syncs every Monday at 00:00 MDT via GitHub Actions. Check-offs persist
across devices via Cloudflare Workers KV.

---

## Architecture

```
Canvas API (naropa.instructure.com)
  │  CANVAS_TOKEN secret (GitHub Actions)
  ▼
sync.py  ──────────────►  dashboard/data.json  ──►  GitHub Pages
                                                      (custom domain via Cloudflare)

Browser  ──►  Cloudflare Worker (dashboard-sync.brooks-1b9.workers.dev)
              │  /state  — GET/PUT check-off state  (KV: STATE namespace)
              │  /dispatch  — POST triggers GitHub Actions workflow_dispatch
              │  Secrets: SHARED_TOKEN, GH_TOKEN
```

- **`sync.py`** — Canvas API → `dashboard/data.json`
- **`dashboard/index.html`** — All UI logic (vanilla JS, no build step)
- **`.github/workflows/sync.yml`** — Monday cron + `workflow_dispatch`
- **`config.json`** — Semester config (no secrets)
- **`worker/`** — Reference copy of Cloudflare Worker code (deployed separately in portal)

---

## Cloudflare Worker — critical detail

**Worker name:** `dashboard-sync`  
**URL:** `https://dashboard-sync.brooks-1b9.workers.dev`  
**Deployed via:** Cloudflare portal (not wrangler from this repo)

The `worker/` folder contains the reference code but the **live worker is managed
in the Cloudflare portal**. If the worker code needs updating, paste the full
replacement into the portal editor and click Deploy.

### Worker secrets (set in portal → Settings → Variables and Secrets)
- `SHARED_TOKEN` — shared between dashboard URL (`#t=…`) and worker `/state`
- `GH_TOKEN` — GitHub PAT with `workflow:write` scope, used by `/dispatch`

### /dispatch — FIXED (verified 2026-08-12, OPTIONS returns 204)
The "Refresh Now" button works. If it ever breaks again, re-paste `worker/index.js`
into the portal and confirm the `GH_TOKEN` secret still exists.

---

## config.json — seed only

NOTE: the `semester` block is AUTO-MAINTAINED (see fact 7). data.json is the
source of truth for what the board is showing; config only seeds the first run.

```json
{
  "canvas_url": "https://naropa.instructure.com",
  "semester": {
    "name": "Fall 2026",
    "start_date": "2026-08-24",
    "weeks": 16,
    "canvas_term_name": "Fall 2026 Semester"
  },
  "excluded_course_ids": [5334, 5337],
  "included_course_ids": null,
  "token_expires": "2026-11-09",
  "instructor_overrides": {
    "CNST-770E": "Jenna Noah"
  }
}
```

`instructor_overrides` maps course code prefix → correct instructor name.
Canvas returns wrong/multiple teachers for some courses; this overrides `teachers[0]`.

---

## sync.py — what it fetches (in order)

For each course, `fetch_course_items()` calls four Canvas endpoints in parallel:

1. `/courses/{id}/assignments` — all published assignments (graded discussions, papers, quizzes, exams)
2. `/courses/{id}/quizzes` — quizzes not already in assignments
3. `/courses/{id}/discussion_topics` — ungraded discussions; uses module-week fallback for items with no `due_at`
4. `/courses/{id}/modules?include[]=items` — module items: Pages expanded via `expand_page_body()`, files, external URLs

**After all per-course passes**, a reconciliation step calls:

5. `/planner/items` (Canvas Planner API) — catches anything the per-course passes missed:
   announcements with deadlines, calendar-event deadlines, dated wiki pages outside modules.
   Only items whose `canvas_id` isn't already in the collected set are added.

### Coverage — MEASURED, not estimated (see fact 10)
Every run publishes three numbers into data.json and onto the dashboard:
- graded coverage (assignments + discussions vs Canvas) — currently 43/43, 25/25
- content recall (module items represented) — currently 77/77
- item quality (% of board that is noise) — currently ~1.4%
The old "~92% weighted" table was an estimate and has been removed; do not
re-introduce estimated confidence numbers when measured ones are available.

### Fallback chain for missing due dates
1. `due_at` (primary)
2. `unlock_at` (when Canvas makes it available — for assignments)
3. Module position (for discussions with no date — `discussion_module_weeks` map)
4. Week = 0 → shown in "No Due Date" section in the UI

### Coverage log
After each sync, the Actions log prints per-course:
- Item counts by source (assignment / quiz / discussion / module_item / page_child)
- Names of graded assignments with no due date
- Names of published Canvas pages not referenced by any module
- Full Planner reconciliation log (every new item added, by course and type)

---

## dashboard/index.html — key functions

| Function | Purpose |
|---|---|
| `boot()` | Fetches data.json, loads KV check-offs, renders |
| `triggerRefresh()` | POSTs to `/dispatch` worker (or legacy `&gh=` PAT path) |
| `pollForFreshData()` | Polls data.json every 5s for up to 180s after dispatch |
| `renderView()` | Renders week columns + undated section |
| `renderWeekColumns(n)` | Course columns for week n |
| `renderUndatedSection()` | Collapsible amber panel for week=0 items |
| `renderProgressSection()` | Progress bar + type pills |
| `renderItem(it)` | Single item row with checkbox, urgency highlight, Note button |
| `filteredItems()` | Items passing current type + course filters |
| `filteredUndatedItems()` | Items with week===0 |
| `pullRemote()` / `flushRemote()` | KV check-off sync via `/state` (tombstone merge) |
| `videoNoteLink(it)` | Optional 📝 Summary link; blocked by the two FERPA regexes |
| `renderSemesterPicker(d)` / `loadSemester(slug)` | Browse archived semesters |
| `adopt_zoom_from_items()` *(sync.py)* | Promotes a Zoom link found in page content onto the course card |

### URL format
```
https://jtbdashboard.fitzsimmons.org/dashboard/#t=<SHARED_TOKEN>
```
The `#t=` fragment is the shared sync token — never appears in server logs (fragment
stays client-side). Without it, check-offs are device-local only.

### Legacy redirect
`https://tbfitzsimmons.github.io/canvas-dashboard/dashboard/` auto-redirects to the
custom domain, preserving the `#t=` hash. Code at top of `<script>` in index.html.

---

## Design facts — do NOT re-litigate these

1. **Check-off sync uses tombstones (v3, since 2026-06-08).** KV + localStorage
   store `id → {c: bool, ts: epoch-ms}`. Merge rule: latest `ts` wins; tie →
   checked wins (legacy-migration parity). An UNCHECK is a `{c:false, ts}`
   record, never an absence — additive-union merging caused unchecks to be
   resurrected by stale devices (bug found 2026-06-08). localStorage key:
   `canvas-dashboard.marks-v3` (migrates v2 array on first load, ts=0).
   The Worker stores the blob verbatim — format changes need NO worker redeploy.
   Known wrinkle (accepted): MARKS is device-global, so after a rollover old-
   semester mark ids can be written into the new semester's KV bucket. Ids are
   globally unique, so this is bloat, not corruption.

2. **Assignment overrides are a NON-issue.** Jennifer's token is student-scoped
   (verified 2026-06-08: StudentEnrollment only). Canvas resolves `due_at` to her
   effective date server-side and hides assignments not assigned to her. Do not
   add override-handling code.

3. **Coverage is continuously self-verified.** Every sync re-fetches
   /assignments + /discussion_topics per course and asserts each published item
   is on the board. Result embedded in data.json as `coverage`; the dashboard
   shows a red banner if anything is missing and a quiet "✓ Verified" line when
   clean. If a refactor ever breaks item emission, the next sync says so on the
   dashboard itself.

4. **iCal export: explicitly declined by Jennifer.** Don't propose it again.

5. **`token_expires` must be COPIED from Canvas, never estimated.** Observed
   lifetimes vary (61 days for the May 2026 token, 90 days for the Aug 2026 one)
   — there is no reliable formula. An estimated value (Sept 5) meant the
   expiry banner never fired; the token died Jul 18 and the sync failed silently
   for 12 runs over 3 weeks. On a 401, sync.py now prints the exact `expired_at`
   Canvas returns — put THAT date in config.json.

6. **Two guards protect the board from silent degradation** (added 2026-08-11):
   - Zero courses matched → hard fail that LISTS the term names Canvas actually
     returns (the one fact needed to fix `canvas_term_name` at rollover).
   - Within the same semester, a course-count drop or >40% item drop → hard fail
     rather than overwrite the last good data.json. Skipped automatically when
     `semester.name` changes (legitimate rollover). Override:
     `SYNC_ALLOW_REGRESSION=1`.

7. **Semester rollover is AUTOMATIC (since 2026-08-12) — do not reintroduce a
   manual checklist.** `pick_active_term()` + `derive_semester()` compute the
   whole `semester` block from Canvas term metadata (`start_at`/`end_at`), which
   was verified to reproduce the hand-written Summer 2026 config exactly (name,
   start_date, canvas_term_name; `weeks` came out 13 vs 12 because Canvas's term
   window runs a week past the last class — harmless trailing empty week).
   Two gates, both required before switching:
     • the candidate term has already STARTED (future terms never displace the
       current board), and
     • it yields >= `rollover_min_items` (default 15) real items — a published
       but empty term must never replace a working board, because to Jennifer
       that is indistinguishable from data loss.
   On switch, `archive_current_semester()` copies the outgoing data.json to
   `dashboard/archive/<slug>.json` + `index.json`; the dashboard's semester
   dropdown loads them read-only (`VIEWING_ARCHIVE` blocks pushes so browsing
   history can't write into an old KV bucket). `.github/workflows/sync.yml`
   stages `dashboard/archive` alongside data.json — verified 2026-08-12.
   Escape hatch: `"auto_rollover": false` in config.json.

8. **Only real academic terms count as rollover candidates.** Canvas returns
   housekeeping buckets ("Default Term", no-term) for library/orientation
   enrollments; `_looks_like_semester()` requires a season word AND a year.
   Without it, "Default Term" pinned a permanent false "new term!" notice
   (observed and fixed 2026-08-12).

9. **The board must never silently degrade — but must not brick either
   (rewritten 2026-08-26).** `assert_no_regression()` distinguishes two cases
   that need opposite responses:
   - ORDINARY roster change (a course dropped/added) → ACCEPTED and published,
     with a `roster_change` record shown on the board for 14 days. The earlier
     version refused ANY course decrease, which stalled the sync for 8 runs over
     2 days when CMHC-609E was legitimately dropped. A safeguard that freezes
     the tool is not a safeguard.
   - CATASTROPHIC loss (all courses gone, >40% of courses gone, or items down
     >40%) → REFUSED, keeping the last good data.json.
   Stands down entirely across a semester change. Override:
   `SYNC_ALLOW_REGRESSION=1`. Verified across 6 boundary cases.

10. **Three metrics, or the claim is false confidence (incident 2026-08-26).**
   For weeks the sync reported "43/43 assignments, 25/25 discussions — nothing
   missing" while 26% of the board (44% of "readings") was noise: professor
   emails, Zoom room links, stock-photo credits, Canvas help docs — all filed as
   coursework by the page-expansion scraper. The coverage number was true and
   the conclusion drawn from it was false, because it measured only graded
   RECALL. Every sync now reports, and data.json carries, all three:
     • graded coverage  (false negatives, graded)      — target 100%
     • content recall   (false negatives, module items) — target 100%
     • item quality     (false positives, whole board)  — noise %, target ~0
   Rules learned the hard way, do not regress:
     • mailto:/tel:/Zoom-join links and stock-credit domains (pixabay,
       thenounproject, …) are never items.
     • Pages titled Instructor Information / Help and Support / Advisor
       Contact / policies are kept as ONE row, never exploded into children.
     • "Online Sourcebook" IS real coursework at Naropa (Jung PDFs, case
       conceptualization templates) — an earlier fix wrongly skipped it by
       title and silently deleted 22 real readings. Filter by what a LINK is,
       not by what a page is called.
     • A metric must state what it does NOT measure. "Nothing missing" and
       "everything here is real" are different claims.

11. **Check-off backups: nightly KV snapshots (added 2026-08-27).** A semester
   of progress otherwise lives in a single KV key. The worker's `scheduled()`
   handler copies `checkoffs` → `backup:<YYYY-MM-DD>` daily and keeps the
   newest 14. Restore needs no portal access — it is three curls:
   ```
   T=<SHARED_TOKEN>; W=https://dashboard-sync.brooks-1b9.workers.dev
   curl -H "Authorization: Bearer $T" $W/backups                  # list dates
   curl -H "Authorization: Bearer $T" $W/backups/2026-08-27 > s.json
   curl -X PUT -H "Authorization: Bearer $T" -H 'Content-Type: application/json' \
        --data-binary @s.json $W/state                            # restore
   ```
   `POST /backups/run` snapshots on demand — use it before anything risky.
   The cron trigger is configured in the Cloudflare portal (Settings → Triggers),
   NOT in this repo; re-add it if the worker is ever recreated.

12. **Video summaries: FERPA line drawn (Brooks, 2026-08-26).** The Naropa
   Archive project may publish `dashboard/video-notes.json` mapping
   `canvas_id → private Drive URL`; the board renders a "📝 Summary" link for
   matching items. It is CONSUMED optionally — absent/empty/malformed/non-https
   → board renders exactly as today (try/catch, verified in browser).
   Ruling: summaries derived from RECORDED CLASS SESSIONS are excluded —
   classmates' voices and names are not hers to surface from a public repo,
   even behind a Google login. Professor lectures are fine.
   Enforced twice on purpose: the archive filters at generation (content-level,
   authoritative) and `CLASS_RECORDING_RE` in index.html refuses to link one
   by title (backstop). Verified on the live board: 24 video/lecture items,
   23 allowed, 1 blocked ("Recordings from Intensive"), 0 false positives.
   If a legitimate lecture ever gets blocked, refine the pattern — do not
   work around it.
   Gap found and closed 2026-08-26 (by the archive session): the three REAL
   class recordings are named `video1855990433.mp4` etc. — Canvas Studio /
   Zoom auto-names that describe nothing and sailed past the descriptive
   patterns. `OPAQUE_MEDIA_RE` now fails closed on those shapes. The lesson:
   the dangerous recordings are the ones with no title to read, so a
   title-based check can never be the primary control — the archive's
   content-based filter is authoritative.

13. **Publishing lecture-note links: `./publish-video-notes.sh`.** The archive
   project (`~/Documents/Naropa Archive`, separate repo, hard rule: it must
   never write here) emits `dashboard-video-notes.json`. That script is the ONE
   sanctioned bridge: it validates (object of canvas_id → https url), copies to
   `dashboard/video-notes.json`, commits and pushes. Safe to re-run; exits
   cleanly on 0 entries or no change. Run it whenever the archive reports new
   notes — there is no automation, by design.

14. **Stale due dates from course copies: `sanitize_due()` in sync.py.** A Fall
   2026 section arrived carrying **2022** due dates (professor copied the course
   without date-shift). Old week-math coerced any past date to week 1, stacking
   14 discussions there. Rule: a due date outside the semester window ±30 days
   is a copy artifact — drop it, fall back to module position or a "Week N" in
   the title, and show NO date rather than a fabricated one.

15. **Zoom links: `adopt_zoom_from_items()` in sync.py.** Course cards get their
   Zoom button from a 5-pass search AND, failing that, by promoting any
   `zoom.us` join link the page-content scan already found. Two fixes collided
   once: the item-quality filter (a Zoom room is not a task) starved the
   adoption step, silently removing two Zoom buttons a day after adding them.
   Rejected Zoom links are now ROUTED to the card via `ZOOM_CANDIDATES`, not
   discarded. If you touch either, test both together.

16. **`robots.txt` + `noindex` suppress INDEXING, not ACCESS (added 2026-08-27).**
   The board is served from the repo root (Pages root = repo root; `/dashboard/`
   is a subdirectory), so `robots.txt` lives at the REPO ROOT — it is only
   honored at the origin root, not under `/dashboard/`. Both entry points
   (`index.html` redirect shim and `dashboard/index.html`) carry
   `<meta name="robots" content="noindex, nofollow">`.
   Do NOT mistake this for a privacy control. It is advisory: compliant
   crawlers stop, nobody else does, and `dashboard/data.json` stays fetchable
   anonymously from raw.githubusercontent.com because THE REPO IS PUBLIC.
   Asked 2026-08-27 whether Google OAuth would remove the FERPA risk — answer
   was NO, and the reason is structural: a login gate on the custom domain is
   a front door on a house with no back wall, and git history is permanent, so
   a leak could never be un-published by a later commit. GitHub Pages cannot
   set response headers, so meta + robots.txt is the ceiling on this host.
   Real access control would require: private repo -> serve from Cloudflare
   Pages/Workers (infra already exists) -> Cloudflare Access with Google IdP.
   Not done; not needed for the FERPA question. The actual control remains
   fact 12 — recorded-class-session summaries are never generated.
   Also: FERPA binds Naropa, not Jennifer. She is not a covered entity. The
   duties here are Naropa's recording policy and decency toward classmates,
   which are CONTENT decisions. No auth gate satisfies them.
   Verified at the time: `data.json` held 0 email addresses across 5 courses
   and 211 items.

17. **Never accept ids/URLs pasted by the archive session — read the file
   (incident 2026-08-27).** The archive session sent "FULL FILE CONTENTS" for
   `dashboard-video-notes.json`. Three of six Drive ids were FABRICATED. Each
   fake matched the real id for the first 26-27 of 33 chars, then diverged.
   Root cause (its own diagnosis): those exact three had been printed
   TRUNCATED WITH AN ELLIPSIS in its earlier tool output, so it had never seen
   the tails and completed them from nothing. Prefix-agreement-then-divergence
   is the fingerprint of a regenerated string; treat it as a red flag anywhere.
   It was a non-event ONLY because `publish-video-notes.sh` copies from the
   file on disk and never from a message. Keep it that way.

   ADJUDICATION METHOD — use this, it settles existence, not equality:
       curl -s -o /dev/null -w "%{http_code}" -A "Mozilla/5.0" \
            "https://drive.google.com/file/d/<id>/view"
   401 = file exists, login required (CORRECT for her private notes).
   404 = no such file. A bogus control id also returns 404, so the two are
   cleanly separable without authenticating. All six published ids return 401.

   PROTOCOL (adopted by both sessions, permanent): the archive session sends
   PATH + ENTRY COUNT + SHA256 only. This session reads the file itself and
   verifies the hash before publishing. Baseline at 6 entries:
   b8515d77d329d4c5d3d69dfd8aa8b0e57ce4fce3fffeb99d301be4d4ea2842a1

   Also re-verify ids against live data.json each time: the join key is
   `canvas_id`, NOT `id`, and page_child ids are md5(title)-derived so they
   drift on rename. 6/6 held this round; that is not a guarantee for next.

   Placement note: most note-bearing items are week 0. Week 0 is the UNDATED
   bucket — it renders in the always-visible section below the week columns
   (`renderUndatedSection()`), not in a week she must navigate to. Do not
   "fix" week 0 by reassigning it to week 1.

18. **Transcripts: narrower than notes, and the FERPA rule now covers them
   (Brooks, 2026-08-27).** Jennifer asked for the raw transcript beside the
   notes. `transcriptLink()` renders "🗒 Transcript" from an OPTIONAL
   `transcript` field on the same `dashboard-video-notes.json` entry — additive,
   https-only, absent field renders nothing.
   It passes through the IDENTICAL `CLASS_RECORDING_RE` / `OPAQUE_MEDIA_RE` gate
   as the notes link and fails closed. Never loosen it: a verbatim transcript of
   a recorded class session is STRICTLY MORE EXPOSING than a summary of one —
   classmates' unedited words, with names.
   Brooks confirmed with Jennifer and ruled: "she does not want class discussion
   wrapped around teaching content transcibed." That covers the transcript
   itself, not just summaries. The archive removed 4 artifacts under it (3
   opaque `video1*` transcripts + 1 captions file); verified absent from disk
   2026-08-27, flagged `removed_class_discussion` in transcripts.json and
   captions.json. Trashed not purged, source video retained, so re-derivable.
   13 transcripts remain, ALL professor lectures, ALL in prior terms — which is
   why fact 19's historical boards are what make this request meaningful.

   THE LESSON, worth more than the rule: the content filter originally ran
   against Canvas media but NOT against YouTube caption tracks. A class session
   that arrived as a YouTube track walked straight past a filter that was
   correct and half-wired. **A control that runs on one input path is not a
   control.** When adding a filter, enumerate every path the content can arrive
   by and prove it runs on each.
   Three layers now, weakest last: (1) content-level at generation in the
   archive — authoritative; (2) the archive's own removal pass; (3) this repo's
   title regexes — a BACKSTOP that cannot see inside `video1855990433.mp4` and
   must never be what saves us.

19. **Historical semester boards: `sync.py --backfill "<term>"` (2026-08-27).**
   Builds a board for a CONCLUDED term and writes ONLY
   `dashboard/archive/<slug>.json` + `index.json`. It never touches data.json,
   the rollover logic, or the regression guard — a backfill is a read of the
   past and must not be able to disturb today's board. Verified byte-identical
   data.json across all four runs.
   Concluded enrollments are INVISIBLE to `enrollment_state=active`, so
   `fetch_raw_courses(states=...)` is parameterised and backfill asks for
   `completed`. All 5 concluded terms carry term `start_at`/`end_at`, so
   `derive_semester()` works unmodified.
   Backfilled 2026-08-27, each at 100% graded coverage:
     Spring 2025  622 items / 4 courses    Summer 2025  566 / 5
     Fall 2025    302 items / 5 courses    Spring 2026  604 / 5
   Archived semesters render READ-ONLY — payload carries `"archived": true`,
   `isReadOnlySemester()` swaps checkboxes for `.ro-dot` and shows "N items"
   instead of x/y. A tick (`.ro-done`) still shows for anything she completed:
   check-off state lives in KV namespaced by semester, so only the tickable
   CONTROL is removed, never the record. Summer 2026 was rebuilt this way too
   (Brooks, 2026-08-28) — 949 → 923 items, because the July-era rollover copy
   predated the quality filters. Graded coverage identical at 49/49 and 33/33;
   the 134 dropped rows were citation fragments, a bare Zoom URL and sentence
   fragments. Noise 2.1%.

20. **Rollover now triggers the CONTENT grab, not just the board copy
   (Brooks, 2026-08-28).** `archive_current_semester()` sets `archived: true`,
   `retired_at`, and `content_archive: "pending"`, and prints a loud ACTION
   REQUIRED block naming the exact commands to pull that term's files into
   Drive. The board shows a warning while viewing any archived semester whose
   `content_archive` is still `pending`.
   WHY THIS EXISTS: archived board links point at CANVAS. Naropa withdraws
   Canvas access after graduation (~May 2027), and on that day every
   un-captured semester becomes unreachable. Brooks's instruction was explicit
   — do not wait to ask the registrar for a retention date, capture it now.
   The Drive side is safe: the account is Jennifer's PERSONAL Google, not a
   Naropa-issued one, so it survives graduation (confirmed 2026-08-28).

21. **`Item.canvas_target` — capture the destination before access ends.**
   A module item's `html_url` is an opaque `/courses/N/modules/items/N`
   redirect that does NOT name what it points at; it can only be dereferenced
   while Canvas answers. `module_item_target()` records it at build time as
   `file:` / `page:` / `assignment:` / `discussion:` / `external:`.
   Captured across all five archived terms: 679/682 module-item rows (99%),
   ~2,700 targets total. Re-pointing archived boards at Drive currently tops
   out at 76% of Canvas links; the residual is 237 assignment pages, 80
   discussion threads and 70 Canvas media embeds. Brooks ruled the first two
   ARE to be snapshotted as HTML into Drive — "it is behind her google drive"
   — which takes it to ~94%. That capture is the ARCHIVE project's job.
   Also found and handed over 2026-08-28: 219 of her own submissions exist in
   Canvas and NOWHERE in the archive — 60 with attachments (67 files), 170
   graded. Her own written work, with feedback. Highest-value, zero FERPA
   question, and it dies with Canvas access like everything else.
   Two bugs found while building it, both worth remembering: the picker read a
   STALE archive list from data.json (a backfill was on disk but unreachable —
   now `loadArchiveIndex()` reads `archive/index.json` directly and wins), and
   progress renders in FOUR places, of which only one had been gated, so one
   semester showed a mix of "31 items" and "0/6". Grep for every render site.

## CURRENT STATE (2026-08-26) — read this before assuming

- Semester: **Fall 2026**, 5 courses, ~211 items. Summer 2026 archived and
  browsable via the dashboard's semester dropdown.
- Course dropped mid-term: CMHC-609E-LB (confirmed by Brooks; the roster-change
  notice is expected, not a bug).
- Canvas token expires **2026-11-09**. Renewal ritual: create token → copy the
  ON-SCREEN expiry into config.json → immediately calendar the NEXT renewal from
  that date. Never estimate the expiry (see fact 5).
- Nightly KV backups: live, cron `0 9 * * *` UTC in the Cloudflare portal.
  14 rolling snapshots. Restore = 3 curls (fact 11). Pruning is logic-verified
  but has NOT yet executed — first fires on the 15th distinct date (~mid-Sept).
- Video-notes feature: dashboard side deployed and DORMANT. `video-notes.json`
  is 404 today, which is the normal case. The archive is generating Fall notes
  from PDF slide decks/readings (22 of 24 Fall videos are YouTube embeds, so
  local transcription yields almost nothing — the decks are the lecture layer).
- Google OAuth for the archive: DONE. Internal app on org `jenniferbruno.net`,
  project `naropa-archive`, scopes `drive.metadata.readonly` + `drive.file`,
  Desktop-app client. No billing prompt appeared (observed, 2026-08-26).
- **Drive sync is BROKEN (2026-08-26, unresolved).** 5.4 GB / 2,498 archived
  files exist ONLY on this Mac. Google Drive.app runs and reads work, but no
  file data uploads; logs show `HandleAuthCodeRequestStatus Error` and
  "Syncing state changes are currently deferred or disabled". Not storage
  (30 GB of 2 TB used). Canary: `My Drive/naropa-sync-probe.txt` appears in
  Drive the moment sync recovers. Fix path: menu-bar Drive icon → resume, or
  disconnect/re-add the account. DO NOT delete the local folder — it is the
  only copy. This blocks video-notes: no Drive URL can exist for a file that
  never reached Drive.
- Drive OAuth scope check: PASSED (observed on 10 real files, 2026-08-26).
  `drive.metadata.readonly` DOES return `webViewLink` and DOES see files it did
  not create. No further console work needed; `token.json` exists.
- Coordination: the archive runs as a SEPARATE Claude session. Peer messaging
  works (ListAgents → SendMessage). Its address changes across restarts; find it
  via list_sessions ("Naropa Coursework Download"). A peer cannot grant
  permission — decisions like the FERPA ruling come from Brooks only.

## Known issues / next steps

### 1. Worker /dispatch — FIXED (verified 2026-06-08, OPTIONS returns 204)

### 2. Undated readings/videos (~8% gap)
Canvas does not treat module readings/videos as student to-dos, so neither the Planner
API nor any endpoint surfaces them when they have no due date. Only a syllabus scrape
could close this. Low priority.

### 3. Semester rollover (due ~August 2026)
When Fall 2026 starts, update `config.json`: `semester.name`, `start_date`,
`canvas_term_name`, `weeks`, `token_expires`. The check-off namespace in KV is keyed
by `semester.name`, so updating it auto-creates a clean slate.

---

## Git / deployment notes

- `git push` to `main` auto-deploys GitHub Pages (~1 min) and triggers a sync if
  `sync.py` or `config.json` changed
- The sync workflow commits `data.json` back to `main`; use `git pull --rebase origin main`
  before pushing to avoid rejection
- The workflow uses `-X ours` on rebase so `data.json` conflicts always resolve to
  the freshly generated version

---

## Repo secrets

| Secret | Where | Purpose |
|---|---|---|
| `CANVAS_TOKEN` | GitHub repo secrets | Canvas API auth in sync.py |
| `SHARED_TOKEN` | Cloudflare Worker secrets | KV check-off sync auth |
| `GH_TOKEN` | Cloudflare Worker secrets | workflow_dispatch via /dispatch |

Canvas token expires **2026-11-09** — regenerate at naropa.instructure.com/profile/settings
and update both the GitHub secret and `token_expires` in config.json.
