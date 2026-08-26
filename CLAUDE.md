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

### Current known issue — MUST FIX
The `/dispatch` endpoint returns **Cloudflare error 1101** (uncaught Worker exception).
Curl test: `curl -X POST https://dashboard-sync.brooks-1b9.workers.dev/dispatch -H "Origin: https://jtbdashboard.fitzsimmons.org"` → `error code: 1101`

**Root cause:** Either (a) `GH_TOKEN` secret is not set in the portal, OR (b) the
deployed worker code has a `cors()` function signature mismatch. The correct full
worker code is in `worker/index.js` — paste that into the portal, deploy, re-test.

---

## config.json — current semester

```json
{
  "canvas_url": "https://naropa.instructure.com",
  "semester": {
    "name": "Summer 2026",
    "start_date": "2026-05-18",
    "weeks": 12,
    "canvas_term_name": "Summer 2026 Semester"
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

### Coverage after Planner reconciliation
| Category | Confidence |
|---|---|
| Graded assignments/quizzes/discussions | 99% |
| Ungraded discussions (dated) | 95% |
| Announcements with due dates | 90% |
| Calendar-event deadlines | 88% |
| Dated pages outside modules | 65% |
| Undated module readings/videos | ~50% (permanent gap — Canvas Planner doesn't list these) |
| **Weighted overall** | **~92%** |

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
| `renderWhatsnext()` | "Up next" strip — 3 nearest-due unchecked items with full dates |
| `renderProgressSection()` | Progress bar + type pills |
| `renderItem(it)` | Single item row with checkbox, urgency highlight, Note button |
| `filteredItems()` | Items passing current type + course filters |
| `filteredUndatedItems()` | Items with week===0 |
| `pullRemote()` / `pushRemote()` | KV check-off sync via `/state` |

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

9. **The board must never silently degrade.** `assert_no_regression()` refuses
   to overwrite data.json when, within the SAME semester, the course count drops
   or items fall >40%. It stands down automatically across a semester change.
   Override with `SYNC_ALLOW_REGRESSION=1`.

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
