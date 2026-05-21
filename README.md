# Canvas Dashboard

A semester-long dashboard for Jennifer that pulls every reading, video, discussion, paper, assignment, quiz, and exam from Canvas (Naropa / Instructure) and presents it as a clean weekly view. Auto-refreshes every Monday at 00:00 MDT via GitHub Actions; readable from any device at:

**https://jtbdashboard.fitzsimmons.org/dashboard/**

## How it works

```
Canvas REST API ──────────────────────────────────────────┐
(naropa.instructure.com)                                  │ CANVAS_TOKEN secret
  • /assignments  • /quizzes  • /discussion_topics         │
  • /modules      • /planner/items (reconciliation)        ▼
                                                    GitHub Actions
                                                    (sync.py — Mondays 00:00 MDT
                                                     + manual "Refresh Now")
                                                          │ commits data.json
                                                          ▼
                                               dashboard/data.json
                                                          │ served by
                                                          ▼
                                               GitHub Pages → Cloudflare DNS
                                               jtbdashboard.fitzsimmons.org

Browser check-offs ──► Cloudflare Worker (dashboard-sync)
                         /state  → KV namespace (cross-device sync)
                         /dispatch → triggers workflow_dispatch via GH_TOKEN
```

- **`sync.py`** — Canvas API → `data.json`. Five-pass coverage: assignments, quizzes, discussions, module items (pages/files/videos), plus a **Canvas Planner API reconciliation pass** that catches announcements, calendar events, and dated pages outside modules. Coverage: ~92%.
- **`dashboard/index.html`** — Static HTML/JS (no build step). Weekly columns, "Up Next" strip, progress bar, urgency highlights, check-off sync via Cloudflare KV. "Refresh Now" button triggers a sync via Cloudflare Worker.
- **`.github/workflows/sync.yml`** — Monday cron + `workflow_dispatch`. Race-condition safe (commit-then-push with `-X ours`).
- **`config.json`** — Semester name, start date, term filter, instructor overrides. Edit once per semester.
- **`worker/`** — Reference copy of the Cloudflare Worker code (live worker deployed in Cloudflare portal as `dashboard-sync`).

The Canvas token never lives in the repo — it's stored as the `CANVAS_TOKEN` GitHub Secret, encrypted by GitHub, only visible to the running workflow.

---

## One-time setup (~10 minutes)

### 1. Add the Canvas token as a GitHub Secret

You should have just regenerated your Canvas token (the previous one was revoked after appearing in chat). On the Canvas "Approved Integrations" page, copy the new token string — Canvas only shows it once.

Then in this repo:

1. Go to **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret**
3. Name: **`CANVAS_TOKEN`** (exact, case-sensitive)
4. Value: paste the token
5. Click **Add secret**

### 2. Enable GitHub Pages

1. Go to **Settings** → **Pages**
2. Source: **Deploy from a branch**
3. Branch: **`main`** / **`/dashboard`** folder
4. Click **Save**
5. Wait ~1 minute. The URL will appear at the top of the Pages settings page — it'll look like `https://tbfitzsimmons.github.io/canvas-dashboard/`

### 3. Trigger the first sync

1. Go to the **Actions** tab
2. Click **Sync Canvas Data** in the left sidebar
3. Click **Run workflow** → **Run workflow** (green button)
4. Wait ~30 seconds. The run will complete with a green checkmark.
5. It will have committed `dashboard/data.json` to the repo.

### 4. Open the dashboard

Visit the Pages URL from step 2. You should see Jennifer's Summer 2026 courses. Bookmark it on every device.

---

## Ongoing use

### For Jennifer

- Open the bookmark on any device.
- Check things off as she finishes them.
- That's it.

### For Brooks (maintenance)

**Weekly:** Nothing. The Monday sync runs automatically.

**If a professor adds content mid-week:**
- Go to the Actions tab → Sync Canvas Data → Run workflow. Takes 30 seconds.
- Or, just wait until Monday.

**Once per semester (Sept / Jan / May)** — ~5 minute checklist:

1. **Regenerate the Canvas token** (Naropa's 120-day cap forces this).
   - Go to https://naropa.instructure.com/profile/settings
   - Scroll to **Approved Integrations** → click **+ New Access Token**
   - Purpose: `Canvas Dashboard`. Leave expiry blank or set ~110 days out.
   - **Copy the token now** — Canvas only shows it once.

2. **Update the GitHub secret** with the new token.
   - Open https://github.com/tbfitzsimmons/canvas-dashboard/settings/secrets/actions
   - Click **CANVAS_TOKEN** → **Update** → paste the new value → **Update secret**.

3. **Update `token_expires` in `config.json`** so the dashboard banner reflects the new expiry.
   - Open https://github.com/tbfitzsimmons/canvas-dashboard/edit/main/config.json
   - Set `"token_expires"` to ~110 days from today, format `YYYY-MM-DD`.

4. **Edit `config.json` for the new semester** (same file, same edit page):
   - `semester.name` — e.g. `"Fall 2026"` (display name on the dashboard).
   - `semester.start_date` — **must be a Monday**, format `YYYY-MM-DD` (e.g. `"2026-08-31"`). All week math derives from this; if it's not a Monday the weeks will be off.
   - `semester.weeks` — `16` for fall/spring, `12` for summer.
   - `semester.canvas_term_name` — **must match Canvas letter-for-letter**, e.g. `"Fall 2026 Semester"` (not `"Fall 2026"`). The sync filters courses by this string; a typo here means zero courses sync.
   - Commit on the GitHub edit page. The push auto-triggers a sync run.

5. **Trigger the workflow manually** (in case the push didn't, or to re-run after fixes).
   - Open https://github.com/tbfitzsimmons/canvas-dashboard/actions/workflows/sync.yml
   - Click **Run workflow** → **Run workflow** (green button). Takes ~30 seconds.

6. **Verify the right courses showed up.**
   - Open https://tbfitzsimmons.github.io/canvas-dashboard/dashboard/
   - Hard refresh (⌘⇧R) once.
   - Check the course filter / sidebar: every class for the new semester should be listed, no leftovers from last term. If a course is missing, `canvas_term_name` is wrong. If an admin pseudo-course shows up, add its course ID to `excluded_course_ids` in `config.json`.
   - Check-offs auto-reset: the dashboard namespaces check-off state by `semester.name`. As soon as you change that field, Jennifer's bookmark loads an empty bucket — no manual KV wipe needed. (Last semester's check-offs stay archived in Cloudflare KV under their own key in case you ever roll back.)

**If something looks wrong** (missing items, wrong classification):
- Open Claude Code in this folder.
- Tell it what's wrong; it will read `sync.py` and the latest `data.json` and fix the rule.
- Commit.

---

## Configuration reference

`config.json`:

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
  "included_course_ids": null
}
```

- **`start_date`** — The Monday of Week 1, `YYYY-MM-DD`. All week math derives from this.
- **`canvas_term_name`** — Must exactly match the term name as Canvas returns it (e.g., `"Summer 2026 Semester"`). The sync filters courses to this term only.
- **`excluded_course_ids`** — Canvas course IDs to skip. Pre-populated with `5334` (Clinical Placement Clearance) and `5337` (MTC Student Center), which are administrative pseudo-courses, not classes.
- **`included_course_ids`** — If non-null, only sync these course IDs. Set to `null` to let the term filter do its job.
- **`instructor_overrides`** — Map of course code prefix → correct instructor name. Used when Canvas returns the wrong first teacher (e.g. for multi-instructor courses).

## Classification rules

How `sync.py` decides what each Canvas item is:

| Dashboard type | Canvas source | Rule |
| --- | --- | --- |
| `exam` | Assignment or Quiz | Title contains "midterm", "final exam", or points ≥ 100 with "final"/"midterm" in name |
| `quiz` | Quiz, or Assignment with `online_quiz` submission | Default for quiz objects |
| `paper` | Assignment | Submission is upload/text AND title contains "paper", "essay", "term paper" |
| `assignment` | Assignment (incl. gradeable discussions) | Everything submittable that isn't paper/quiz/exam |
| `discussion` | Discussion topic | Only ungradeable discussions (gradeable ones → assignment) |
| `video` | Module item (Page or ExternalUrl) | Title or URL contains video keywords (youtube, vimeo, zoom rec, "lecture video", etc.) |
| `reading` | Module item (Page, File, ExternalUrl, ExternalTool) | Default for content items that aren't video |

Rules live in `sync.py` near the top — search for `VIDEO_HINTS`, `PAPER_HINTS`, `EXAM_HINTS`. To change a rule, edit there and commit.

---

## Troubleshooting

**"Couldn't load dashboard data" on the page**
The first sync hasn't run yet. Go to Actions → Sync Canvas Data → Run workflow.

**Action fails with "Canvas rejected the token (401)"**
The token expired (Naropa caps at 120 days). Regenerate it on Canvas, update the `CANVAS_TOKEN` secret.

**Action fails with "No active courses matched"**
The `canvas_term_name` in `config.json` doesn't match what Canvas returns. Check it letter-for-letter (e.g., `"Summer 2026 Semester"` vs `"Summer 2026"`).

**Missing items**
The classification rules may have skipped them. Open Claude Code, describe what's missing, ask it to adjust. Or, file an issue against the repo as a note-to-self with the Canvas link to the missing item.

**Course showing up that shouldn't (e.g., a Student Center pseudo-course)**
Add its Canvas ID to `excluded_course_ids` in `config.json`.

**Items in the wrong week**
Either (a) the item has no due date and was bucketed by module name pattern, or (b) the `start_date` in config is wrong. Check both.

---

## File map

```
canvas-dashboard/
├── .github/workflows/sync.yml    # Monday cron + manual dispatch (race-condition safe)
├── CLAUDE.md                      # Claude Code context — read this for full project state
├── README.md                      # This file
├── config.json                    # Semester settings + instructor overrides (no secrets)
├── config.example.json            # Template
├── sync.py                        # Canvas API → data.json (5-pass, ~92% coverage)
├── worker/
│   ├── index.js                   # Cloudflare Worker reference code
│   └── wrangler.toml              # Worker config (worker deployed via Cloudflare portal)
└── dashboard/
    ├── index.html                 # Full dashboard UI (vanilla JS, no build)
    └── data.json                  # Generated — do not edit by hand
```

> **For Claude Code:** Open `CLAUDE.md` for full architecture, current issues, and handoff context.
