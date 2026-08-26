# Canvas Dashboard

A semester-long dashboard for Jennifer that pulls every reading, video, discussion, paper, assignment, quiz, and exam from Canvas (Naropa / Instructure) and presents it as a clean weekly view. Auto-refreshes every Monday at 00:00 MDT via GitHub Actions; readable from any device at:

**https://jtbdashboard.fitzsimmons.org/dashboard/**

> Building something else against Canvas? **[CANVAS-API-NOTES.md](CANVAS-API-NOTES.md)**
> collects the portable API lessons (token lifetimes, hidden next-term courses,
> stale course-copy dates, timezone traps).
>
> Want one of your own? See **[FORKING.md](FORKING.md)** — a checklist for
> turning a fork into your own deployment (~1 hour, no coding required).

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

- **`sync.py`** — Canvas API → `data.json`. Five-pass coverage: assignments, quizzes, discussions, module items (pages/files/videos), plus a **Canvas Planner API reconciliation pass** that catches announcements, calendar events, and dated pages outside modules. Every run ends with a **three-metric self-audit**, published in `data.json` and shown on the dashboard: **graded coverage** (every assignment/discussion in Canvas is on the board), **content recall** (every module item is represented), and **item quality** (% of the board that looks like noise rather than coursework). Anything missing or unrepresented flips a red banner naming the items. Roster changes (a dropped/added course) are accepted and announced on the board for 14 days rather than blocking the sync.
- **`dashboard/index.html`** — Static HTML/JS (no build step). Weekly columns, progress bar, urgency highlights. "Refresh Now" button triggers a sync via Cloudflare Worker. **Check-offs sync across devices** via timestamped records in Cloudflare KV — checks *and unchecks* propagate within ~30s; latest toggle wins on conflict. Each device pairs once by opening the bookmark URL (`#t=…`); the token then persists on-device. The masthead's **📥 backup** link downloads a JSON snapshot; "unpair this device" + reopening the bookmark re-pairs.
- **`.github/workflows/sync.yml`** — Monday cron + `workflow_dispatch`. Race-condition safe (commit-then-push with `-X ours`).
- **`config.json`** — Semester name, start date, term filter, instructor overrides. Edit once per semester.
- **`publish-video-notes.sh`** — The one sanctioned bridge from the separate
  Naropa Archive project: validates and publishes `dashboard/video-notes.json`
  (canvas_id → private Drive URL), which makes a "📝 Summary" link appear on
  matching lecture items. Run it when the archive reports new notes; it is safe
  to re-run and exits cleanly with nothing to do. Summaries derived from
  recorded CLASS SESSIONS are excluded (FERPA) and blocked twice — at
  generation and again by the dashboard.
- **`worker/`** — Reference copy of the Cloudflare Worker code (live worker deployed in Cloudflare portal as `dashboard-sync`).

The Canvas token never lives in the repo — it's stored as the `CANVAS_TOKEN` GitHub Secret, encrypted by GitHub, only visible to the running workflow.

---

## One-time setup (~10 minutes)

### 1. Add the Canvas token as a GitHub Secret

On the Canvas "Approved Integrations" page (Account → Settings → + New Access Token), copy the token string — Canvas only shows it once.

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

**Semester rollover: nothing to do.** As of Aug 2026 this is automatic. Every
sync reads Canvas's own term metadata and derives the semester name, start date
(snapped to the Monday of the term's first week), length, and exact term name.
It switches only when the new term has **started** AND its courses actually
contain content (≥15 items) — so a published-but-empty Fall shell never replaces
a working board. When it switches it archives the outgoing semester to
`dashboard/archive/<slug>.json`; that term stays browsable from the semester
dropdown on the dashboard, and its check-offs remain in their own Cloudflare KV
bucket, untouched. Watch it happen in the sync log:

```
ℹ terms with active enrollments: Fall 2026 Semester, Summer 2026 Semester
→ Candidate new term detected: "Fall 2026 Semester"
  ✓ Fall 2026 Semester: 5 course(s), 312 items — content is live, rolling over
  📦 archived 'Summer 2026' → dashboard/archive/summer-2026.json (949 items retained)
```

To pin a semester manually anyway, set `"auto_rollover": false` in `config.json`
and edit the `semester` block by hand.

**The one recurring chore: renewing the Canvas token** (~3 min, roughly every
60–90 days — the dashboard warns you 14 days ahead):

1. **Create a new token.**
   - https://naropa.instructure.com/profile/settings
   - **Approved Integrations** → **+ New Access Token**
   - Purpose: `Canvas Dashboard`. Leave the expiry field blank.
   - **Write down the expiry date Canvas shows you** — you need it in step 3.
     Naropa's token lifetime VARIES (observed: 61 days in May 2026, 90 days in
     Aug 2026). Never assume; use the date on screen.
   - **Copy the token now** — Canvas only shows it once.

2. **Update the GitHub secret.**
   - https://github.com/tbfitzsimmons/canvas-dashboard/settings/secrets/actions
   - **CANVAS_TOKEN** → **Update** → paste → **Update secret**.

3. **Update `token_expires` in `config.json`** to that exact date.
   - https://github.com/tbfitzsimmons/canvas-dashboard/edit/main/config.json
   - **Do not estimate it.** In July 2026 an estimated date (Sept 5) hid a real
     expiry (July 18); the sync failed silently for 12 runs over 3 weeks before
     anyone noticed. Committing this file auto-triggers a sync.

4. **Confirm a green run:**
   https://github.com/tbfitzsimmons/canvas-dashboard/actions/workflows/sync.yml
   A 401 failure now prints the exact expiry Canvas reports, so the log tells you
   what's wrong without any guesswork.

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

- **`semester`** — Auto-maintained. On rollover the sync overwrites these from Canvas's
  term metadata; you only edit them if you set `"auto_rollover": false`.
  - `start_date` — Monday of Week 1, `YYYY-MM-DD`. All week math derives from this.
  - `canvas_term_name` — Must match the Canvas term name exactly; the sync filters to it.
- **`auto_rollover`** — Defaults to `true`. Set `false` to pin the semester by hand.
- **`rollover_min_items`** — Defaults to `15`. How much content a new term must have
  before the board switches to it (guards against empty published shells).
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
The token expired. The sync log prints the exact expiry Canvas reports. Regenerate it on
Canvas, update the `CANVAS_TOKEN` secret, **and** update `token_expires` in `config.json`
to the real date Canvas shows.

**Action fails with "No active courses matched"**
Normally self-correcting — rollover is automatic. If you've set `auto_rollover: false`, the
`canvas_term_name` in `config.json` must match Canvas letter-for-letter. Either way the sync
log prints every term name Canvas returns, so copy one from there.

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
├── sync.py                        # Canvas API → data.json (5-pass + 3-metric self-audit)
├── worker/
│   ├── index.js                   # Cloudflare Worker reference code
│   └── wrangler.toml              # Worker config (worker deployed via Cloudflare portal)
└── dashboard/
    ├── index.html                 # Full dashboard UI (vanilla JS, no build)
    └── data.json                  # Generated — do not edit by hand
```

> **For Claude Code:** Open `CLAUDE.md` for full architecture, current issues, and handoff context.
