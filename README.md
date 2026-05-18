# Canvas Dashboard

A semester-long dashboard for Jennifer that pulls every reading, video, discussion, paper, assignment, quiz, and exam from Canvas (Naropa / Instructure) and presents it as a clean weekly view. Auto-refreshes every Monday at 00:00 MDT via GitHub Actions; readable from any device (iMac, MacBook Air, iPad) at the same URL.

## How it works

```
   ┌─────────────────┐    every Monday 00:00     ┌──────────────────┐
   │ Canvas REST API │ ────────────────────────► │  GitHub Actions  │
   │ (naropa.inst..) │      (sync.py runs)       │  (sync workflow) │
   └─────────────────┘                           └────────┬─────────┘
                                                          │ commits
                                                          ▼
                                              ┌─────────────────────┐
                                              │ dashboard/data.json │
                                              └──────────┬──────────┘
                                                         │ served by
                                                         ▼
                                              ┌─────────────────────┐
                                              │   GitHub Pages       │
                                              │  (dashboard URL)     │
                                              └─────────────────────┘
```

- **`sync.py`** — Talks to Canvas via the API token. Pulls active courses for the current term, then every assignment, quiz, discussion topic, and module item (pages, files, videos). Classifies each into the right type. Writes `dashboard/data.json`.
- **`dashboard/index.html`** — Static HTML. Loads `data.json`, renders the weekly view. Auto-detects "current week" from the semester start date. Check-offs save to the local browser.
- **`.github/workflows/sync.yml`** — Runs `sync.py` on a cron schedule (Mondays 00:00 MDT) and on manual trigger.
- **`config.json`** — Semester name, start date, term filter. Edit once per semester.

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

**Once per semester (Sept / Jan / May):**
- Regenerate the Canvas token (Naropa's 120-day cap forces this).
- Update the `CANVAS_TOKEN` repo secret with the new value.
- Edit `config.json`:
  - `semester.name` (e.g., `"Fall 2026"`)
  - `semester.start_date` (Monday of Week 1)
  - `semester.weeks` (typically 16 for fall/spring, 12 for summer)
  - `semester.canvas_term_name` (e.g., `"Fall 2026 Semester"` — must match Canvas exactly)
- Commit. The push triggers an immediate sync.

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
- **`included_course_ids`** — If non-null, only sync these course IDs. Use this if term filtering picks up courses you don't want, or you only want a subset. Set back to `null` to let the term filter do its job.

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
├── .github/workflows/sync.yml    # Monday cron + manual dispatch
├── .gitignore                     # Protects against committing secrets
├── README.md                      # This file
├── config.json                    # Semester settings (committed, no token)
├── config.example.json            # Template
├── sync.py                        # Canvas API → data.json
└── dashboard/
    ├── index.html                 # The dashboard (the only thing Pages serves)
    └── data.json                  # Generated by sync.py — do not edit by hand
```
