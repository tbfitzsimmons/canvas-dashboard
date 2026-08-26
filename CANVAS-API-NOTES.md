# Canvas API — hard-won notes (Naropa / Instructure)

Portable lessons from building the Canvas Dashboard, extracted so **sibling
projects** (e.g. a course-content archiver) don't relearn them the hard way.
Nothing here is dashboard-specific. Every item below cost real debugging time.

Reference implementation: `sync.py` in this repo.
Raw: https://raw.githubusercontent.com/tbfitzsimmons/canvas-dashboard/main/sync.py

---

## 1. Token

- Base URL: `https://naropa.instructure.com`, auth via `Authorization: Bearer <token>`.
- Created at Account → Settings → **Approved Integrations → + New Access Token**.
- **Lifetime varies and is capped server-side.** Observed: 61 days (May 2026),
  90 days (Aug 2026). There is **no formula** — record the expiry Canvas shows
  on screen. An estimated expiry once hid a real one by 7 weeks; the sync failed
  silently for 12 runs over 3 weeks.
- A 401 body carries the truth — surface it instead of guessing:
  ```json
  {"errors":[{"message":"Expired access token.","expired_at":"2026-07-18T00:00:00Z"}]}
  ```
- Jennifer's token is **student-scoped** (`StudentEnrollment` only). This matters:
  Canvas resolves due-date overrides server-side and hides assignments not
  assigned to her, so per-section override handling is unnecessary.
- Never commit a token. GitHub Actions secret, or a gitignored local config.

## 2. Pagination

Canvas paginates via the `Link` header, not a body cursor:

```python
link = r.headers.get("Link", "")
for part in link.split(","):
    if 'rel="next"' in part:
        next_url = part.split(";")[0].strip(" <>")
```

`per_page=100` is the practical max. **`/planner/items` caps at 50.**

## 3. Retry — Canvas throttles and hiccups

Retry `429` and `5xx` with exponential backoff; honor `Retry-After` when present.
Also catch `ConnectionError`/`Timeout` — an unretried transient failure kills a
whole run. Do **not** retry `401` (it will never succeed).

## 4. Courses: `enrollment_state` hides the next semester

`GET /courses?enrollment_state=active` does **not** return courses in a term that
hasn't opened yet — the enrollment sits in `invited_or_pending`. A student can be
browsing a Fall course in the Canvas UI while the API insists it doesn't exist.
Fetch **both** states and merge, deduping by course id:

```python
for state in ("active", "invited_or_pending"):
    canvas.paginate("/courses", {"enrollment_state": state, "include[]": ["term","teachers"]})
```

## 5. Terms

`include[]=term` returns `{id, name, start_at, end_at}`. The term object is
authoritative enough to derive a whole semester config (verified: it reproduced a
hand-written config exactly, except `weeks`, which runs ~1 long because the term
window extends past the last class).

Beware junk terms: Canvas returns housekeeping buckets like **"Default Term"**
(and null terms) for library/orientation enrollments. Require a season word AND a
year before treating a term as a real semester.

Known non-course enrollments to exclude at Naropa: course ids **5334**
(Clinical Placement Clearance) and **5337** (MTC Student Center).

## 6. Dates — two traps

**Timezone.** Canvas stores UTC. A "Sunday 11:59 PM MDT" deadline serializes as
`2026-06-01T05:59:59Z` — a *Monday* in UTC. Do all date math and weekday labels
in `America/Denver` (`zoneinfo.ZoneInfo`), or items land in the wrong week.

**Course copies carry stale dates.** When a professor copies a course forward
without the date-shift tool, old due dates come with it — observed: a Fall 2026
Internship section arriving with 14 discussions dated **2022**. Treat a due date
outside the semester window (±30 days) as a copy artifact, not a deadline.
Fall back to module position, or to a "Week N" named in the title.

## 6b. Course copies carry stale due dates

When a professor copies a course forward without Canvas's date-shift tool, old
due dates ride along. Observed: a Fall 2026 section arrived with 14 discussions
dated **2022**; naive week-math coerced "past date" to week 1 and stacked the
whole semester's discussions there. Treat any due date outside the semester
window (±30 days) as a copy artifact — fall back to module position or a
"Week N" named in the title, and leave the date blank rather than fabricating one.

## 6c. Page bodies are full of non-coursework links

Expanding page HTML into items WILL sweep up `mailto:`/`tel:` contact details,
Zoom room links, stock-image credits (pixabay, thenounproject), and platform
help links. On one real board that was 26% of all items before filtering.
Filter by what a LINK is, not what its page is called — title-based page
skipping deleted 22 real readings ("Online Sourcebook" held actual course PDFs).
And measure noise separately from coverage: "nothing missing" and "everything
here is real" are different claims.

## 7. Feature endpoints 404 when disabled

`/quizzes`, `/discussion_topics`, and `/modules` return **404** (not an empty
list) when the professor has disabled that feature for a course. Swallow 404
specifically; surface every other HTTP error.

## 8. Graded discussions are two objects

A graded discussion appears as BOTH an `/assignments` entry and a
`/discussion_topics` entry, and again in `/planner/items`. Dedupe by following
`discussion_topic.assignment_id` — otherwise every graded discussion shows twice.

## 9. Useful endpoints beyond the obvious

| Endpoint | Why |
|---|---|
| `/courses/{id}/modules?include[]=items` | Module structure; items carry `page_url` for Pages |
| `/courses/{id}/pages/{page_url}` | Page HTML body (`body` field) |
| `/courses/{id}/files` | **File download URLs — the entry point for an archiver** |
| `/courses/{id}/folders` | Folder tree, for mirroring structure |
| `/courses/{id}/students/submissions?student_ids[]=self` | What she already submitted |
| `/courses/{id}/tabs` | Finds LTI tabs — Naropa's Zoom lives at "Online Events" |
| `/planner/items` | Canvas's own to-do view; good reconciliation net |
| `/users/self/enrollments` | Confirm scope (student vs teacher) |

Module items reference Pages by **`page_url` slug**, not `content_id` — matching
Pages by content_id silently fails.

## 10. Working principle

Verify against the live API rather than trusting the code or these notes. Every
lesson above started as a confident assumption that turned out to be wrong. When
something looks right, check the actual response before saying so.
