#!/usr/bin/env python3
"""
build_archive_urls.py — re-point ARCHIVED board links at Google Drive.

WHY THIS EXISTS
    Archived board links point at Canvas (`/courses/N/modules/items/N` and
    friends). Naropa withdraws Canvas access after graduation (~May 2027) and on
    that day every one of those links dies. The content itself is already in
    Jennifer's PERSONAL Google Drive, which survives graduation. This script
    writes an optional `archive_url` onto each archived board row so the board
    has a durable alternative to offer.

    It is deliberately a SCRIPT, not a one-off: the archive keeps growing, so
    this must be re-runnable. It rebuilds `archive_url` from scratch every run,
    which means a row whose mapping disappears loses its `archive_url` rather
    than keeping a stale one.

WHAT IT TOUCHES
    WRITES  dashboard/archive/*.json   (adds/updates/removes `archive_url` only)
    READS   ~/Documents/Naropa Archive/{manifest,assignment_pages,discussions,
                                        drive_index}.json
    NEVER   dashboard/data.json (the CURRENT semester) — not read, not written.
    NEVER   anything under ~/Documents/Naropa Archive — opened read-only, and
            the archive belongs to a different project's sessions.

THE JOIN
    board row ──┬─ direct /courses/N/files/N link ───────► manifest.files
                ├─ direct /courses/N/pages/SLUG link ────► manifest.pages
                ├─ direct /courses/N/assignments/N ──────► assignment_pages.json
                ├─ direct /courses/N/discussion_topics/N ► discussions.json
                └─ else Item.canvas_target
                        file:C:ID / page:C:SLUG /
                        assignment:C:ID / discussion:C:ID ► same four tables
                                                         │
                                 {path: "<relpath>"} ────┘
                                                         ▼
                              drive_index.json["files"][relpath].webViewLink

TWO KINDS OF HIT, AND THE DIFFERENCE MATTERS
    exact      `archive_url` is the Drive copy of the resource THIS ROW NAMES.
               Direct-link matches, and `module_item` rows (whose canvas_target
               records the redirect's real destination — see CLAUDE.md fact 21).

    container  The row is a `page_child`: a child link lifted out of a Canvas
               page's body by the page-expansion scraper. For those rows
               canvas_target names the page the row was EXTRACTED FROM, not the
               child's own destination (verified: two children of module item
               308233 both carry `page:7419:lecture-s-3`). When the child's own
               target cannot be resolved, `archive_url` points at the Drive HTML
               snapshot of that containing page — the page where the content
               lived. Useful and durable, but it is not the row's own artifact,
               so it is counted and reported separately. Never silently merged
               into the headline number.

WHAT IS REFUSED ON PURPOSE
    media embeds   `/media_objects_iframe/` and `/media_attachments_iframe/`
                   rows have NO Drive equivalent. Their containing page IS in
                   Drive, so the container rule would happily map all 70 of
                   them — and the result would be a "Drive copy" link that
                   opens an HTML snapshot whose embedded player is itself a
                   dead Canvas URL. A link that looks like it worked is worse
                   than no link. These keep their Canvas link.
    external:      canvas_target already records the real off-Canvas URL
                   (YouTube, counseling.org). Nothing to fetch from Drive.
                   Reported so the residual is explained rather than hidden.
    discussion gaps Canvas refused these to Jennifer (`user_can_see_posts`
                   false), so the captured HTML is a stub. Recorded as
                   permanently unavailable, not linked.

TITLE AND LINK ONLY
    This script writes a URL. It never writes note text, source text or page
    body onto the board, and it never un-strips `summary`. Archived boards carry
    zero body text and must continue to.

USAGE
    python3 build_archive_urls.py                 # rebuild + report
    python3 build_archive_urls.py --dry-run       # report only, write nothing
    python3 build_archive_urls.py --archive-root ~/somewhere/else
    python3 build_archive_urls.py --misses out.json   # dump every unmapped row
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import sys

# Display order only. This used to be the list of boards the script PROCESSED,
# which meant a newly archived semester was skipped without a word: at the Fall
# 2026 rollover, `fall-2026.json` would have been written to disk, never looked
# at, and its ~200 rows left with no archive_url, while the report simply did
# not mention it. A silent omission reads exactly like a clean run. Boards are
# now DISCOVERED (index.json, then the directory) and anything unrecognised is
# processed and named, not dropped.
SEMESTER_ORDER = ["spring-2025", "summer-2025", "fall-2025", "spring-2026", "summer-2026"]


def discover_boards(board_dir: str) -> list[str]:
    """Every board slug present, ordered: known semesters first, then the rest.

    Sources, unioned so no board can hide: archive/index.json (authoritative,
    written by sync.py when a semester is archived) and the directory listing
    (catches a board written but not yet indexed).
    """
    slugs: list[str] = []

    idx_path = os.path.join(board_dir, "index.json")
    if os.path.exists(idx_path):
        try:
            with open(idx_path, encoding="utf-8") as fh:
                idx = json.load(fh)
            for e in (idx if isinstance(idx, list) else idx.get("archives") or []):
                sl = e.get("slug") if isinstance(e, dict) else None
                if sl and sl not in slugs:
                    slugs.append(sl)
        except (OSError, ValueError) as exc:
            print(f"  ⚠  archive/index.json unreadable ({exc}); falling back to the "
                  f"directory listing")

    on_disk = sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(board_dir, "*.json"))
                     if os.path.basename(p) != "index.json")
    unindexed = [s for s in on_disk if s not in slugs]
    if unindexed:
        print(f"  ⚠  board file(s) present but not in index.json: {', '.join(unindexed)} "
              f"— processing anyway")
        slugs += unindexed

    missing = [s for s in slugs if not os.path.exists(os.path.join(board_dir, f"{s}.json"))]
    if missing:
        print(f"  ⚠  index.json lists board(s) with no file: {', '.join(missing)}")

    known = [s for s in SEMESTER_ORDER if s in slugs]
    return known + [s for s in slugs if s not in known]

CANVAS_HOST = re.compile(r"naropa\.instructure\.com", re.I)
MEDIA_EMBED = re.compile(r"/(media_objects_iframe|media_attachments_iframe)/", re.I)
# Statuses seen as of 2026-11-25: manifest "archived", assignment/discussion
# "done", discussion "prompt_only". Anything else still resolves if it has a
# path; it is only reported, so a new archive vocabulary cannot silently break
# the board — nor silently pass unnoticed.
KNOWN_STATUSES = {None, "archived", "done", "prompt_only"}
UNFAMILIAR_STATUSES: "collections.Counter[str]" = collections.Counter()

MEDIA_ATTACH_RE = re.compile(r"/media_attachments_iframe/(\d+)", re.I)
MEDIA_OBJECT_RE = re.compile(r"/media_objects_iframe/(m-[A-Za-z0-9]+)", re.I)
MODULE_ITEM_RE = re.compile(r"/courses/(\d+)/modules/items/(\d+)", re.I)


def _course_from_target(it) -> str:
    """Course id out of canvas_target ("page:7235:slug" -> "7235"). Media embed
    URLs carry no course, and the file key needs one."""
    parts = (it.get("canvas_target") or "").split(":")
    return parts[1] if len(parts) >= 3 and parts[1].isdigit() else ""
MODULE_REDIRECT = re.compile(r"/modules/items/\d+|/module_item_redirect/\d+", re.I)

# Direct-resource link shapes. Order is irrelevant (they are mutually exclusive)
# but the table name must match the keys of TABLES below.
LINK_RE = {
    "file": re.compile(r"/courses/(\d+)/files/(\d+)"),
    "page": re.compile(r"/courses/(\d+)/pages/([^/?#]+)"),
    "assignment": re.compile(r"/courses/(\d+)/assignments/(\d+)"),
    "discussion": re.compile(r"/courses/(\d+)/discussion_topics/(\d+)"),
}

# `canvas_target` kinds that name a Drive-backed artifact. `external:` is a real
# kind but is not one of these — it points off Canvas entirely.
TARGET_KINDS = set(LINK_RE)


# ── loading ────────────────────────────────────────────────────────────────────

def load_tables(archive_root: str):
    """Read the four lookup tables and the Drive index. READ-ONLY."""

    def rd(name):
        p = os.path.join(archive_root, name)
        if not os.path.exists(p):
            sys.exit(f"FATAL: {p} not found. Pass --archive-root.")
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)

    manifest = rd("manifest.json")
    tables = {
        "file": manifest.get("files", {}),
        "page": manifest.get("pages", {}),
        "assignment": rd("assignment_pages.json"),
        "discussion": rd("discussions.json"),
        # Added 2026-11-25 for media the archive recovered after the first pass.
        # Absent until the archive writes them; an empty table simply refuses,
        # which is the correct behaviour before the upload lands.
        "media": manifest.get("media", {}),              # bare Kaltura "m-…" id
        "module_item": manifest.get("module_items", {}),  # "<course>:<item id>"
    }
    drive = rd("drive_index.json").get("files", {})
    return tables, drive


def resolve(tables, drive, kind, key):
    """(kind, key) -> (webViewLink, reason). reason is None on success."""
    table = tables.get(kind)
    if table is None:
        return None, "unknown_kind"
    entry = table.get(key)
    if not isinstance(entry, dict):
        return None, f"{kind}_not_in_archive"
    # "gap" meant Canvas refused the topic to her. The archive established
    # (2026-11-25) that it only ever meant the REPLIES were refused: the topic
    # and its prompt were archived inside the page HTML all along, and those
    # records are now "prompt_only". A prompt-only discussion is a perfectly
    # good artifact — it is the assignment she was given — so it resolves.
    # Replies stay absent, which is both Canvas's answer and Jennifer's ruling.
    #
    # Resolvability is decided by HAVING A PATH, not by a status string. A first
    # attempt at this gated on an allow-list of statuses and immediately dropped
    # 296 working rows, because assignment_pages and discussions both use "done"
    # and the list did not know it. The archive owns these vocabularies and may
    # extend them; the dashboard must not break when it does. So: refuse only
    # the one status that is known to mean "no artifact exists", report anything
    # unfamiliar, and let the no-path check below handle the rest.
    status = entry.get("status")
    if status == "gap":
        return None, "discussion_gap_unavailable"
    if status not in KNOWN_STATUSES:
        UNFAMILIAR_STATUSES[f"{kind}:{status}"] += 1
    path = entry.get("path")
    if not path:
        return None, f"{kind}_no_path"
    hit = drive.get(path)
    if not isinstance(hit, dict):
        return None, f"{kind}_not_in_drive"
    url = hit.get("webViewLink")
    if not isinstance(url, str) or not url.startswith("https://"):
        return None, f"{kind}_bad_webviewlink"
    url = scrub_url(url)
    if not url:
        return None, f"{kind}_unrecognised_drive_url"
    return url, None


# Drive's own webViewLink shapes. Anything else is not emitted rather than
# emitted unexamined — this is the only place a URL reaches a public repo.
DRIVE_URL_RE = re.compile(
    r"^https://(?:drive\.google\.com/file/d/[A-Za-z0-9_-]+/view"
    r"|docs\.google\.com/(?:document|presentation|spreadsheets)/d/[A-Za-z0-9_-]+/edit)"
)


def scrub_url(url: str):
    """
    Drop the query string from a Drive webViewLink, and refuse any URL that is
    not a shape we recognise.

    WHY: Drive returns `?usp=drivesdk&ouid=<21-digit account id>&rtpof=…` on
    files it converted to native Docs formats. `ouid` is Jennifer's STABLE
    GOOGLE ACCOUNT ID — 212 of these links carried it. THIS REPO IS PUBLIC
    (CLAUDE.md fact 16: data.json is fetchable anonymously from
    raw.githubusercontent.com) and git history is permanent, so a later commit
    could never un-publish it. The links work identically without the query
    string: verified unauthenticated, stripped and unstripped forms return the
    same status for the same file.
    """
    base = url.split("?", 1)[0].split("#", 1)[0]
    return base if DRIVE_URL_RE.match(base) else None


# ── the ruleset ────────────────────────────────────────────────────────────────

def plan_row(it, tables, drive):
    """
    Decide this row's archive_url.
    Returns (url_or_None, tier, category) where tier is 'exact'|'container'|None
    and category explains a miss (or names the winning rule on a hit).
    """
    link = it.get("link") or ""
    if not link or not CANVAS_HOST.search(link):
        return None, None, "not_a_canvas_link"

    # Media embeds. This used to be a flat refusal — "the item IS the media, and
    # the media is in no Drive" — which was true until 2026-11-24, when the 40
    # Kaltura objects and 58 page-embedded files were recovered and archived.
    # A refusal that was a fact about the world became a refusal that hid one,
    # so it now RESOLVES and only refuses when the lookup genuinely fails.
    #
    # Two shapes, measured across all 70 such rows on the board:
    #   /media_attachments_iframe/<canvas file id>   (23) -> files table
    #   /media_objects_iframe/<m-kaltura id>         (37) -> media table
    # Neither URL carries a course id, so the file key takes its course from
    # canvas_target, which on every one of these is "page:<course>:<slug>".
    m = MEDIA_ATTACH_RE.search(link)
    if m:
        course = _course_from_target(it)
        if not course:
            return None, None, "media_attachment_no_course"
        url, why = resolve(tables, drive, "file", f"{course}:{m.group(1)}")
        if url:
            return url, "exact", "link:media_attachment"
        return None, None, why or "media_attachment_not_in_archive"

    m = MEDIA_OBJECT_RE.search(link)
    if m:
        # Kaltura ids are globally unique; the same object is embedded in more
        # than one course, so the key carries no course prefix.
        url, why = resolve(tables, drive, "media", m.group(1))
        if url:
            return url, "exact", "link:media_object"
        return None, None, why or "media_object_not_in_archive"

    if MEDIA_EMBED.search(link):
        # A media embed whose handle we could not parse at all.
        return None, None, "media_embed_unparsed"

    # Canvas Studio lectures arrive as ExternalTool module items: the item id is
    # the only stable handle, since these rows carry no canvas_target and
    # /api/v1/courses/:id/modules/items/<id> 404s for them.
    m = MODULE_ITEM_RE.search(link)
    if m:
        url, why = resolve(tables, drive, "module_item", f"{m.group(1)}:{m.group(2)}")
        if url:
            return url, "exact", "link:module_item"
        # Not fatal: fall through, the ordinary rules may still match.

    # 1. The row's own link names a specific resource. Most precise signal there
    #    is — prefer it over canvas_target, which for a page_child names only
    #    the page the row was scraped out of.
    for kind, rx in LINK_RE.items():
        m = rx.search(link)
        if not m:
            continue
        key = f"{m.group(1)}:{m.group(2)}"
        url, why = resolve(tables, drive, kind, key)
        if url:
            return url, "exact", f"link:{kind}"
        direct_miss = why  # remember it; canvas_target may still save the row
        break
    else:
        direct_miss = None

    # 2. Fall back to the destination captured at sync time while Canvas still
    #    answered. For module_item rows this IS the destination (exact). For
    #    page_child rows it is the containing page (container).
    target = it.get("canvas_target") or ""
    kind, _, key = target.partition(":")
    if not key:
        return None, None, direct_miss or ("no_canvas_target" if MODULE_REDIRECT.search(link)
                                           else "unresolvable_canvas_link")
    if kind == "external":
        return None, None, "external_url_not_in_drive"
    if kind not in TARGET_KINDS:
        return None, None, f"unknown_target_kind:{kind}"

    url, why = resolve(tables, drive, kind, key)
    if not url:
        return None, None, direct_miss or why

    tier = "container" if it.get("source") == "page_child" else "exact"
    return url, tier, f"target:{kind}"


# ── control ───────────────────────────────────────────────────────────────────

def run_control(tables, drive):
    """
    A check that cannot fail is not a check (CLAUDE.md facts 10, 22).
    Prove the join DISCRIMINATES before trusting anything it says: feed it rows
    that must NOT map, and a row that must.
    """
    print("CONTROL (negative cases must all refuse, positive must map):")
    cases = [
        ("bogus file id", {"link": "https://naropa.instructure.com/courses/7981/files/999999999",
                           "canvas_target": "", "source": "module_item"}, False),
        ("bogus page slug", {"link": "https://naropa.instructure.com/courses/7981/modules/items/1",
                             "canvas_target": "page:7981:no-such-page-xyz", "source": "module_item"}, False),
        ("bogus course", {"link": "https://naropa.instructure.com/courses/999999/files/1264128",
                          "canvas_target": "", "source": "module_item"}, False),
        ("media embed w/ real parent page", {"link": "https://naropa.instructure.com/media_objects_iframe/m-xyz?type=audio",
                                             "canvas_target": "page:7981:about-your-instructor-emily-weir",
                                             "source": "page_child"}, False),
        ("external target", {"link": "https://naropa.instructure.com/api/v1/courses/7208/module_item_redirect/1",
                             "canvas_target": "external:https://youtube.com/watch?v=x",
                             "source": "module_item"}, False),
        ("non-canvas link", {"link": "https://example.org/thing", "canvas_target": "", "source": "module_item"}, False),
        ("REAL file", {"link": "https://naropa.instructure.com/courses/7981/files/1264128",
                       "canvas_target": "", "source": "module_item"}, True),
        ("REAL page via target", {"link": "https://naropa.instructure.com/courses/7981/modules/items/1",
                                  "canvas_target": "page:7981:about-your-instructor-emily-weir",
                                  "source": "module_item"}, True),
    ]
    # The URL scrubber is the last thing between a Drive link and a public repo,
    # so prove it strips, preserves and refuses rather than assuming it does.
    # The ouid below is DELIBERATELY all zeros. A real one is a stable Google
    # account identifier, and this file lives in a public repo with permanent
    # history -- the exact exposure the scrubber exists to prevent. Never paste
    # a real value here, not even to make the fixture look authentic.
    scrub_cases = [
        ("https://drive.google.com/file/d/ABC-123_x/view?usp=drivesdk",
         "https://drive.google.com/file/d/ABC-123_x/view"),
        ("https://docs.google.com/document/d/ABC/edit?usp=drivesdk&ouid=000000000000000000000&rtpof=true",
         "https://docs.google.com/document/d/ABC/edit"),
        ("https://drive.google.com/file/d/ABC/view", "https://drive.google.com/file/d/ABC/view"),
        ("https://evil.example.com/file/d/ABC/view", None),
        ("http://drive.google.com/file/d/ABC/view", None),
    ]
    ok = True
    for name, row, want in cases:
        url, tier, cat = plan_row(row, tables, drive)
        got = url is not None
        mark = "ok " if got == want else "FAIL"
        if got != want:
            ok = False
        print(f"  [{mark}] {name:34} expect={'map' if want else 'refuse':6} got={'map' if got else 'refuse':6} ({cat})")
    for raw, want_url in scrub_cases:
        got_url = scrub_url(raw)
        good = got_url == want_url
        if not good:
            ok = False
        print(f"  [{'ok ' if good else 'FAIL'}] scrub {raw[:52]:52} -> {str(got_url)[:46]}")
    if not ok:
        sys.exit("CONTROL FAILED — the join does not discriminate. Refusing to write.")
    print("  control passed: the join can both map and refuse; scrubber strips, keeps and rejects.\n")


# ── main ───────────────────────────────────────────────────────────────────────


def run_synthetic_control() -> bool:
    """Prove the media / module-item / prompt_only rules can MAP, not just refuse.

    The live control runs against the real archive, which cannot exercise these
    until the archive session's upload lands — so a rule could sit here for days
    looking fine while being incapable of ever succeeding. These cases use a
    hand-built archive in exactly the shape the archive session committed to
    emitting, so both directions are proven on every run.
    """
    B = "https://naropa.instructure.com"
    tables = {
        "file": {"7235:1033337": {"status": "archived", "path": "P/vid.mp4"}},
        "page": {}, "assignment": {},
        "discussion": {"7208:80089": {"status": "prompt_only", "path": "P/disc.html"},
                       "7208:99999": {"status": "gap", "path": "P/stub.html"}},
        "media": {"m-REAL": {"status": "archived", "path": "P/kal.m4a"}},
        "module_item": {"7617:338198": {"status": "archived", "path": "P/mi.mp4"}},
    }
    drive = {p_: {"webViewLink": f"https://drive.google.com/file/d/ID{i}/view"}
             for i, p_ in enumerate(["P/vid.mp4", "P/disc.html", "P/kal.m4a",
                                     "P/mi.mp4", "P/stub.html"])}
    cases = [
        ("media attachment maps", {"link": f"{B}/media_attachments_iframe/1033337",
         "canvas_target": "page:7235:m1", "source": "page_child"}, "link:media_attachment"),
        ("media attachment absent", {"link": f"{B}/media_attachments_iframe/999",
         "canvas_target": "page:7235:m1", "source": "page_child"}, "file_not_in_archive"),
        ("media attachment no course", {"link": f"{B}/media_attachments_iframe/1033337",
         "canvas_target": "", "source": "page_child"}, "media_attachment_no_course"),
        ("kaltura maps", {"link": f"{B}/media_objects_iframe/m-REAL?type=video",
         "canvas_target": "page:7235:x", "source": "page_child"}, "link:media_object"),
        ("kaltura absent", {"link": f"{B}/media_objects_iframe/m-NOPE",
         "canvas_target": "page:7235:x", "source": "page_child"}, "media_not_in_archive"),
        ("studio module item maps", {"link": f"{B}/courses/7617/modules/items/338198",
         "canvas_target": "", "source": "module_item"}, "link:module_item"),
        ("prompt_only maps", {"link": f"{B}/courses/7208/discussion_topics/80089",
         "canvas_target": "", "source": "discussion"}, "link:discussion"),
        ("true gap refused", {"link": f"{B}/courses/7208/discussion_topics/99999",
         "canvas_target": "", "source": "discussion"}, "discussion_gap_unavailable"),
    ]
    ok = True
    for name, item, expect in cases:
        _url, _tier, cat = plan_row(item, tables, drive)
        good = cat == expect
        ok &= good
        print(f"  [{'ok ' if good else 'FAIL'}] {name:26} expect={expect:28} got={cat}")
    print("  synthetic control passed: media, module-item and prompt_only rules can "
          "MAP and REFUSE." if ok else "  SYNTHETIC CONTROL FAILED — not writing boards.")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive-root", default=os.path.expanduser("~/Documents/Naropa Archive"),
                    help="read-only source of manifest.json / drive_index.json / etc.")
    ap.add_argument("--board-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                        "dashboard", "archive"),
                    help="directory holding the archived board JSON files")
    ap.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    ap.add_argument("--misses", metavar="PATH", help="write every unmapped Canvas link to PATH as JSON")
    args = ap.parse_args()

    tables, drive = load_tables(args.archive_root)
    print(f"archive  : {args.archive_root}")
    print(f"boards   : {args.board_dir}")
    print("tables   : " + ", ".join(f"{k}={len(v)}" for k, v in tables.items())
          + f", drive_index={len(drive)}\n")

    run_control(tables, drive)
    if not run_synthetic_control():
        sys.exit(2)

    grand = collections.Counter()
    misses = collections.Counter()
    miss_rows = []
    rules = collections.Counter()
    per_sem = []

    for slug in discover_boards(args.board_dir):
        path = os.path.join(args.board_dir, f"{slug}.json")
        if not os.path.exists(path):
            print(f"  (skip {slug}: no board file)")
            continue
        with open(path, "r", encoding="utf-8") as fh:
            board = json.load(fh)

        items = board.get("items", [])
        n_canvas = n_exact = n_container = 0
        changed = 0

        for it in items:
            had = it.get("archive_url")
            url, tier, cat = plan_row(it, tables, drive)

            # Rebuilt from scratch every run: a mapping that goes away must take
            # its archive_url with it rather than leaving a stale URL behind.
            if url:
                it["archive_url"] = url
            elif "archive_url" in it:
                del it["archive_url"]
            if had != it.get("archive_url"):
                changed += 1

            link = it.get("link") or ""
            if not link or not CANVAS_HOST.search(link):
                continue
            n_canvas += 1
            if tier == "exact":
                n_exact += 1
                rules[cat] += 1
            elif tier == "container":
                n_container += 1
                rules[cat + " (container)"] += 1
            else:
                misses[cat] += 1
                miss_rows.append({"semester": slug, "title": it.get("title"),
                                  "type": it.get("type"), "source": it.get("source"),
                                  "link": link, "canvas_target": it.get("canvas_target"),
                                  "reason": cat})

        mapped = n_exact + n_container
        per_sem.append((slug, len(items), n_canvas, n_exact, n_container, mapped))
        grand["items"] += len(items)
        grand["canvas"] += n_canvas
        grand["exact"] += n_exact
        grand["container"] += n_container
        grand["changed"] += changed

        if not args.dry_run:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(board, fh, ensure_ascii=False, indent=2)
                fh.write("\n")

    # ── report ────────────────────────────────────────────────────────────────
    def pct(a, b):
        return f"{a / b * 100:5.1f}%" if b else "    —"

    print("COVERAGE PER SEMESTER (of Canvas links on that board)")
    print(f"  {'semester':12} {'items':>6} {'canvas':>7} {'exact':>7} {'contain':>8} "
          f"{'mapped':>7} {'mapped%':>8}")
    for slug, n_items, n_canvas, n_exact, n_cont, mapped in per_sem:
        print(f"  {slug:12} {n_items:6} {n_canvas:7} {n_exact:7} {n_cont:8} "
              f"{mapped:7} {pct(mapped, n_canvas):>8}")
    g_mapped = grand["exact"] + grand["container"]
    print(f"  {'TOTAL':12} {grand['items']:6} {grand['canvas']:7} {grand['exact']:7} "
          f"{grand['container']:8} {g_mapped:7} {pct(g_mapped, grand['canvas']):>8}")
    print(f"\n  exact      {grand['exact']:5}  {pct(grand['exact'], grand['canvas'])} "
          f" — Drive copy of the resource the row itself names")
    print(f"  container  {grand['container']:5}  {pct(grand['container'], grand['canvas'])} "
          f" — Drive HTML of the Canvas page the row was extracted from")

    print("\nWINNING RULE")
    for rule, n in rules.most_common():
        print(f"  {n:5}  {rule}")

    if UNFAMILIAR_STATUSES:
        print("\nUNFAMILIAR STATUSES (resolved anyway — reported so a new archive "
              "vocabulary cannot pass unnoticed)")
        for k, v in UNFAMILIAR_STATUSES.most_common():
            print(f"  {v:5}  {k}")

    print("\nCOULD NOT MAP")
    total_miss = sum(misses.values())
    for cat, n in misses.most_common():
        print(f"  {n:5}  {cat}")
    print(f"  {total_miss:5}  TOTAL unmapped Canvas links "
          f"({pct(total_miss, grand['canvas'])} of Canvas links)")

    print(f"\nrows whose archive_url changed this run: {grand['changed']}")
    if args.dry_run:
        print("DRY RUN — no board file was written.")
    else:
        print("archived board files rewritten. dashboard/data.json untouched.")

    if args.misses:
        with open(args.misses, "w", encoding="utf-8") as fh:
            json.dump(miss_rows, fh, ensure_ascii=False, indent=2)
        print(f"unmapped rows written to {args.misses}")


if __name__ == "__main__":
    main()
