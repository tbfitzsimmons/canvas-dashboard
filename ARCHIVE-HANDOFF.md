# Archive ↔ dashboard status

Written by the dashboard session. Coordination happens through files, not
messages — no session here outlives its conversation. Read this when you start.

Last updated: 2026-09-11 08:00

---

## Everything previously open here is DONE

All items in the 2026-09-11 07:20 version of this file were completed by the
two archive sessions the same morning. Verified independently by the dashboard
session against disk and the live boards, not taken on report.

| | Was | Now |
|---|---|---|
| `prior_notes.json` written flag | 6 of 200 on disk | 181 |
| Reading-note links on the boards | 0 | **197 rendering** (251-key contract) |
| Discussions | 155 done / 21 gap | 170 done / 6 gap |
| Submissions | 204, every status `null` | 210, all `done` |
| Assignment pages | 236, every status `null` | 268, all `done` |
| Files on one machine only | 46–49 | **0** |
| Validation gate | 17 PASS, stale since 09-05 | 21 PASS, 0 FAIL, 0 WARN |

**Durability, verified by walking the tree:** 4,272 local files, 6.70 GB,
0 absent from Drive. Nothing in the archive exists in only one place.

## Live contracts

Both are in sync between disk and the published board. Re-publish with
`./publish-video-notes.sh` / `./publish-reading-notes.sh` after any change.

| contract | entries | renders |
|---|---|---|
| `dashboard-video-notes.json` | 17 (13 with transcript) | Fall 2026 |
| `dashboard-reading-notes.json` | 251 keys / 194 docs | 5 archived semesters |

30 of the 251 keys reach no board item. Checked: they are real Canvas files the
board does not surface as items. They render nothing, and they stay in the
contract because a future board rebuild may surface them.

## Still open, and who owns it

- **6 discussion gaps** — `user_can_see_posts=false`. Canvas will not serve
  these to Jennifer at all. Not recoverable by anyone; record them as
  permanently unavailable rather than leaving them looking unfinished.
- **12 sources genuinely unreadable** — reason recorded beside each. She has
  the PDFs; she lacks searchable text.
- **Drive re-pointing** (dashboard session). Archived board links still point
  at Canvas. `canvas_target` is captured on 679/682 module-item rows, so this
  can be done offline later — 76% today, ~94% now that assignment-page and
  discussion HTML exist. **Deadline is Canvas access ending after graduation
  (~May 2027).** Nobody has asked the registrar for the real date.

## A gate check worth adding (parent fork's suggestion, endorsed)

`index: reaches all notes` confirms the 312 notes on disk are linked, but does
not assert that the **state files can find them**. Today that check would have
failed on 26 notes written beside a non-canonical copy, and the reading-notes
contract would never have shipped 25 entries short.

## The recurring failure, three costumes in one day

Every significant bug here was a value that looked measured and was not:

1. `written` — a cached scan with no invalidation trigger. It could never
   become true.
2. `notes_path` — a correct field describing a stale intention (the canonical
   copy) while agents wrote beside whichever copy was in their batch.
3. A leak check that resolved 22 of 30 links and printed PASS.

**A cached value with no invalidation reads exactly like a measured one.**
Compare against disk. Run the control first. A check that cannot fail on
known-bad input is not a check.

## Standing constraints

- **Title and link only on the board.** Archived boards carry zero body text.
  Notes render as a link into authenticated Drive, never note text or source
  text. Copyright is the live issue for readings; GitHub Pages is public and
  Drive enforces auth.
- **No transcript and no summary for recorded class discussion.** Four
  artifacts removed under this ruling; verified absent.
- **Contract handoff:** path + entry count + sha256. Never contents, never ids.
  The dashboard session reads from disk and verifies the hash before publishing.
