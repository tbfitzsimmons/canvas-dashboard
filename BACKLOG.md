# Backlog

Maintained by the dashboard session. Ordered by value, with the deadline that
drives each. Everything here is scoped against measurements, not estimates —
the numbers come from probes run 2026-11-01/02.

**The deadline behind most of this:** Naropa withdraws Canvas access after
graduation (~May 2027). Nobody has asked the registrar for the real date, so
treat May 2027 as the outside bound. Her Google Drive is a PERSONAL account and
survives graduation; Canvas does not.

---

## 1. Syllabi, grades, rubrics — ARCHIVE PROJECT, deadline-driven, APPROVED

**Approved by Jennifer 2026-11-02. Prompt written; needs an archive session.**

⚠️ **REPRIORITISED.** This item was first raised as "grades and CACREP KPI
rubrics". Jennifer corrected it: licensure needs **transcripts and syllabi**,
and she does not expect to need the KPI rubrics. Checking what she named
exposed a far bigger gap than the one originally flagged:

```
syllabus files on disk:  6 total, ALL in Summer 2025
  Spring 2025  0     Spring 2026  0
  Fall 2025    0     Summer 2026  0     Fall 2026  0
Canvas syllabus_body:    8 of 31 courses — captured as such: 0
```

Likely cause: the archive walks modules and pages, but Canvas's Syllabus tab is
a separate surface and `syllabus_body` is a course attribute, not a module item
— so it was never in the crawl path. Same shape as the YouTube-captions FERPA
gap: a control that runs on one input path is not a control.

Transcripts come from the REGISTRAR, not Canvas — not an archive task.

Priority within this item: syllabi first, then course grades, then rubrics.

Measured against Canvas today:

```
course final grades : 17 of 25 concluded courses have one — captured NOWHERE
rubric assessments  : 9 in a 115-submission sample (~8%) — captured 0 of 210
```

Why it matters more than it looks: the rubrics attach to **CACREP Key
Performance Indicator** assignments — the accreditation competencies the
program is measured against.

```
CMHC-600 · Advocacy Project (CMHC 3 KPI)       CMHC-603 · Video Assignment #3 (E9 KPI)
CMHC-603 · Self-Care Practice Paper (C7 KPI)   CMHC-606 · Code of Ethics Paper (A10 KPI)
```

A transcript shows a letter grade. It does not show rubric scores against named
competencies, and those are what a licensure board or clinical supervisor asks
for. This exists in Canvas today and nowhere else.

Already covered, do not redo: per-submission `score` (183/210) and `comments`
(210/210) ARE captured.

Endpoints: `GET /courses?include[]=total_scores` for course grades;
`GET /courses/:id/students/submissions?include[]=rubric_assessment` per course.

Owner: the archive project (writes into `~/Documents/Naropa Archive`). The
dashboard session never writes there.

## 2. Re-point archived board links at Drive — ✅ DONE 2026-11-01

Archived board links point at Canvas and die with it. `canvas_target` is already
captured on 679/682 module-item rows precisely so this can be done offline,
after access ends if necessary.

```
re-pointable today                                        76% of 2,109 links
with assignment-page + discussion HTML (now captured)    ~94%
residual: ~70 Canvas media embeds, no Drive equivalent
```

Design agreed: an optional `archive_url` per item; archived boards prefer Drive
and fall back to Canvas. Additive — nothing breaks where a mapping is missing.
Join path: board item → `canvas_target` or `/courses/N/files/M` → `manifest.json`
→ `drive_index.json` → `webViewLink`.

## 3. Self-contained offline copy of the board — ✅ DONE 2026-11-01

Everything currently assumes `tbfitzsimmons/canvas-dashboard` keeps existing. If
that lapses, six semesters of navigable index vanish while 6.7 GB of content
sits in Drive with no way to browse it.

Emit a single HTML file with all data inlined, no fetches, and drop it in Drive
beside the archive. None of the comparable projects do this —
`canvas-student-data-export` (276★) produces raw JSON and HTML with no
navigation layer. Ours IS the navigation layer, and it is the fragile part.

### Outcome (verified independently by the dashboard session, not taken on report)

**Item 2 — `build_archive_urls.py`, re-runnable.** 1,952 of 2,113 Canvas links
re-pointed, **92.4%**: 1,763 exact + 189 container fallbacks. Unmapped 161:
85 external targets, 70 Canvas media embeds, 3 discussion gaps, 3 with no
`canvas_target`. 30 URLs probed, 0 bad; bogus-id control 404s.

A naive join scores 95.8% and is WRONG — for `page_child` rows `canvas_target`
names the CONTAINING page, so it maps all 70 media embeds onto a parent whose
embedded player is itself a dead Canvas URL. The residual *count* looked right
while its *composition* was wrong.

⚠️ A real Google account id (`ouid`) was scrubbed from every board URL and then
hardcoded into the scrubber's own TEST FIXTURE in the public repo. Removed in
`28f6748`; **commit b56fc83 still carries it in history** and only a force-push
would purge it. Brooks's call; assessed low severity (an identifier, not a
credential).

**Item 3 — `export_offline.py`.** One 1.67 MiB HTML file, 6 semesters, 3,254
items, 9 payloads inlined. Verified in real headless Chrome on `file://`:
exactly ONE network request (the file itself) with a deliberate off-box control
proving the log would have caught more; 0 console errors; all six semesters
switch; 0 `.item-summary` in archived DOM. Output is gitignored — the script is
tracked, her data is not.

A durable copy lives at `~/Documents/Canvas Dashboard Offline/` with a README.
**It belongs in Google Drive** — a copy on one laptop is not an archive. The
generated file had been sitting in `/tmp`, which macOS reaps (it ate this
project's working clone twice).

### Follow-up left open, needs a design decision

The 85 **external** links (33 youtube.com, 8 naropa.zoom.us, 4 counseling.org…)
still die with Canvas: the board points at a `module_item_redirect` while the
real destination already sits in `canvas_target`. Re-pointing them takes
durability from 92.4% to ~96.4%. NOT done, deliberately — `archive_url` renders
as "📁 Drive copy" and a YouTube URL behind that label would be a lie. It needs
a second field or a varying label.

## 4. Point an assistant at the ARCHIVE, not at Canvas — SCOPING NEEDED

`canvas-mcp` (310★) is the best thing in this space: local, read-only, ~100
student tools. But it reads Canvas, which expires. The durable version aims the
same idea at her Drive archive, useful through the 2-3 years of post-graduate
supervision and licensure prep.

Brooks also asked whether this could serve her **Taos Pueblo RecoveryWorks**
work. Unknown to this project — needs scoping with him before any design.

## Standing constraints

- **Title and link only on the board.** Archived boards carry zero body text.
  Copyright is the live issue for readings; GitHub Pages is public, Drive
  enforces auth.
- **No transcript or summary for recorded class discussion.**
- **Contract handoff:** path + entry count + sha256. Never contents, never ids.
- **Canvas token:** 30-day cap, renewed in place by `extend_tokens()`. Scopes
  are recorded but NOT enforced on this instance, so the token has full user
  rights and that cannot be reduced from her account.
