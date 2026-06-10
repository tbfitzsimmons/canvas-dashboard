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
  "token_expires": "2026-09-05",
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

Canvas token expires **2026-09-05** — regenerate at naropa.instructure.com/profile/settings
and update both the GitHub secret and `token_expires` in config.json.
