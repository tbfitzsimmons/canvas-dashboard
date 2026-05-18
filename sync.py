#!/usr/bin/env python3
"""
Canvas Dashboard Sync
=====================
Pulls every course, assignment, quiz, discussion, page, and module item from
Canvas via the REST API, classifies each item into the dashboard's seven
content types, and writes dashboard/data.json.

Run locally:    CANVAS_TOKEN=xxx python3 sync.py
Run on Actions: token comes from the CANVAS_TOKEN secret automatically.

Designed to be re-run safely — fully idempotent. Writes data.json atomically
so the dashboard never sees a half-written file.
"""

from __future__ import annotations
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import requests

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"
DATA_PATH = ROOT / "dashboard" / "data.json"

# Course color palette — assigned in enrollment order.
PALETTE = [
    {"name": "indigo",     "bar": "#3D4A7C", "tag_bg": "#DDE0EB", "tag_fg": "#38426C"},
    {"name": "teal",       "bar": "#1A7F93", "tag_bg": "#D2E6EA", "tag_fg": "#0F5763"},
    {"name": "sage",       "bar": "#4F8045", "tag_bg": "#DCE9D5", "tag_fg": "#345A2E"},
    {"name": "ochre",      "bar": "#B0832C", "tag_bg": "#F1E5C9", "tag_fg": "#7A5A1C"},
    {"name": "rose",       "bar": "#CB3F77", "tag_bg": "#F4D7E3", "tag_fg": "#8A2A52"},
    {"name": "plum",       "bar": "#8366B4", "tag_bg": "#E2D8F0", "tag_fg": "#5A4683"},
    {"name": "rust",       "bar": "#A24E36", "tag_bg": "#F0DDD2", "tag_fg": "#7A3A28"},
    {"name": "slate",      "bar": "#626E7B", "tag_bg": "#DEE3E8", "tag_fg": "#3F4853"},
]

# Item-type classification keywords.
VIDEO_HINTS = [
    "video", "lecture video", "recorded lecture", "recording",
    "youtube", "vimeo", "zoom recording", "panopto", "kaltura",
    "watch", "screencast",
]
PAPER_HINTS = ["paper", "essay", "term paper", "reflection paper", "final paper"]
EXAM_HINTS = ["midterm", "final exam", "final assessment", "exam", "comprehensive"]

# Canvas pagination — pull everything.
PER_PAGE = 100


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Item:
    week: int
    courseId: str
    type: str         # reading | video | discussion | paper | assignment | quiz | exam
    title: str
    detail: str = ""
    due: str = "—"    # short day-of-week e.g. "Thu"
    due_date: str | None = None  # ISO date for sorting
    link: str = ""
    points: float | None = None
    canvas_id: str = ""           # stable id from Canvas — used to preserve check-off state
    source: str = ""              # which Canvas object: assignment | quiz | discussion | page | module_item


@dataclass
class Course:
    id: str           # internal id, e.g. "course1"
    canvas_id: int
    code: str
    name: str
    instructor: str
    palette: dict = field(default_factory=dict)
    canvas_url: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Canvas API client
# ─────────────────────────────────────────────────────────────────────────────

class Canvas:
    def __init__(self, base_url: str, token: str):
        self.base = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/json+canvas-string-ids, application/json",
        })

    def _get(self, path: str, params: dict | None = None) -> Any:
        """Single GET, returns parsed JSON."""
        url = path if path.startswith("http") else f"{self.base}/api/v1{path}"
        r = self.session.get(url, params=params, timeout=30)
        if r.status_code == 401:
            raise SystemExit(
                "❌ Canvas rejected the token (401 Unauthorized).\n"
                "   Your token may have expired (Naropa caps tokens at 120 days).\n"
                "   Regenerate at: Account → Settings → New Access Token\n"
                "   Then update the CANVAS_TOKEN secret in GitHub."
            )
        r.raise_for_status()
        return r

    def paginate(self, path: str, params: dict | None = None) -> Iterable[dict]:
        """Yield all items across paginated Canvas responses."""
        params = {**(params or {}), "per_page": PER_PAGE}
        url = f"{self.base}/api/v1{path}"
        while url:
            r = self._get(url, params=params)
            data = r.json()
            if isinstance(data, list):
                yield from data
            else:
                yield data
                return
            # Canvas uses Link headers for pagination
            next_url = None
            link = r.headers.get("Link", "")
            for part in link.split(","):
                if 'rel="next"' in part:
                    next_url = part.split(";")[0].strip(" <>")
            url = next_url
            params = None  # already in next_url


# ─────────────────────────────────────────────────────────────────────────────
# Classification
# ─────────────────────────────────────────────────────────────────────────────

def lower(s: str | None) -> str:
    return (s or "").lower()


def classify_assignment(a: dict) -> str:
    """Decide what an Assignment object represents."""
    name = lower(a.get("name"))
    desc = lower(a.get("description") or "")
    points = a.get("points_possible") or 0
    submission_types = a.get("submission_types") or []

    # Discussion-as-assignment: Canvas creates an Assignment row for gradeable discussions.
    if "discussion_topic" in submission_types:
        return "assignment"  # gradeable discussion → assignment per Jennifer's rule

    # Exam check
    if any(h in name for h in EXAM_HINTS):
        return "exam"
    if points >= 100 and ("final" in name or "midterm" in name):
        return "exam"

    # Quiz-as-assignment
    if "online_quiz" in submission_types:
        # Will be picked up by quizzes endpoint too; treat assignment row as quiz.
        return "quiz"

    # Paper check
    if "online_upload" in submission_types or "online_text_entry" in submission_types:
        if any(h in name for h in PAPER_HINTS) or any(h in desc for h in PAPER_HINTS[:3]):
            return "paper"

    return "assignment"


def classify_module_item(item: dict, content: dict | None = None) -> str | None:
    """Decide what a ModuleItem (page/file/external_url) represents. Returns None to skip."""
    item_type = item.get("type", "")
    title = lower(item.get("title"))
    url = lower(item.get("external_url") or item.get("html_url") or "")

    if item_type in ("Assignment", "Quiz", "Discussion"):
        return None  # handled by their own endpoints, skip to avoid duplicates

    if item_type == "SubHeader":
        return None

    # Video detection — strongest signal first
    if any(h in title for h in VIDEO_HINTS):
        return "video"
    if any(domain in url for domain in ["youtube.com", "youtu.be", "vimeo.com", "panopto", "kaltura", "zoom.us/rec"]):
        return "video"

    # Everything else that's content → reading
    if item_type in ("Page", "File", "ExternalUrl", "ExternalTool"):
        return "reading"

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Week math
# ─────────────────────────────────────────────────────────────────────────────

def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        # Canvas returns Z-suffixed ISO 8601
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def week_number(due: datetime | None, semester_start: datetime, total_weeks: int) -> int:
    if not due:
        return 0  # "unscheduled" bucket
    days = (due.date() - semester_start.date()).days
    if days < 0:
        return 1  # pre-semester item — bucket into week 1
    wk = days // 7 + 1
    return min(max(wk, 1), total_weeks)


def due_day_short(due: datetime | None) -> str:
    if not due:
        return "—"
    return due.strftime("%a")  # "Mon", "Tue", etc.


# ─────────────────────────────────────────────────────────────────────────────
# Sync
# ─────────────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"❌ Missing {CONFIG_PATH}. Copy config.example.json to config.json and edit.")
    with CONFIG_PATH.open() as f:
        return json.load(f)


def get_token(cfg: dict) -> str:
    # Priority: env var (Actions secret), then config file (local dev convenience).
    token = os.environ.get("CANVAS_TOKEN") or cfg.get("canvas_token", "")
    if not token or token.startswith("<"):
        raise SystemExit(
            "❌ No Canvas token found.\n"
            "   Local dev: put it in config.json under 'canvas_token'\n"
            "   GitHub Actions: add it as the CANVAS_TOKEN repo secret"
        )
    return token


def select_courses(canvas: Canvas, cfg: dict) -> list[Course]:
    """Pull active courses, filter to current term, assign internal IDs and colors."""
    print("→ Fetching active courses…")
    raw = list(canvas.paginate("/courses", {
        "enrollment_state": "active",
        "include[]": ["term", "teachers"],
    }))

    term_filter = cfg.get("semester", {}).get("canvas_term_name")
    if term_filter:
        raw = [c for c in raw if (c.get("term") or {}).get("name") == term_filter]

    # Optional manual exclusion (e.g., student-center pseudo-courses)
    excluded_ids = set(cfg.get("excluded_course_ids", []))
    raw = [c for c in raw if c.get("id") not in excluded_ids and str(c.get("id")) not in excluded_ids]

    # Optional manual override of which courses to include
    included_ids = cfg.get("included_course_ids")
    if included_ids:
        included_ids = {str(x) for x in included_ids}
        raw = [c for c in raw if str(c.get("id")) in included_ids]

    courses = []
    for i, c in enumerate(raw):
        teachers = c.get("teachers") or []
        instructor = teachers[0].get("display_name") if teachers else ""
        courses.append(Course(
            id=f"course{i+1}",
            canvas_id=int(c["id"]),
            code=c.get("course_code", "").split(".")[0],  # strip ".2026SU" suffix
            name=clean_course_name(c.get("name", "")),
            instructor=instructor,
            palette=PALETTE[i % len(PALETTE)],
            canvas_url=f"{canvas.base}/courses/{c['id']}",
        ))
    print(f"  ✓ {len(courses)} active course(s): {', '.join(c.code for c in courses)}")
    return courses


def clean_course_name(name: str) -> str:
    """Strip course code and term from Canvas's long name. e.g.
    'CMHC-607E-LB 2026SU Diag & Treatmnt Planning' → 'Diag & Treatmnt Planning'"""
    # Remove leading code-like token and term
    name = re.sub(r"^[A-Z]+-\d+[A-Z]?(-[A-Z]+)?\s+", "", name)
    name = re.sub(r"\b\d{4}(SP|SU|FA|WI)\b\s*", "", name)
    return name.strip()


def fetch_course_items(canvas: Canvas, course: Course, cfg: dict) -> list[Item]:
    """Pull all four content sources for one course in parallel."""
    semester_start = parse_iso(cfg["semester"]["start_date"]) or datetime.now(timezone.utc)
    total_weeks = cfg["semester"]["weeks"]
    cid = course.canvas_id

    items: list[Item] = []
    seen_assignment_ids: set[int] = set()

    # 1. Assignments
    try:
        for a in canvas.paginate(f"/courses/{cid}/assignments"):
            if not a.get("published", True):
                continue
            due = parse_iso(a.get("due_at"))
            kind = classify_assignment(a)
            items.append(Item(
                week=week_number(due, semester_start, total_weeks),
                courseId=course.id,
                type=kind,
                title=a.get("name", "Untitled"),
                detail=points_detail(a),
                due=due_day_short(due),
                due_date=due.isoformat() if due else None,
                link=a.get("html_url", ""),
                points=a.get("points_possible"),
                canvas_id=f"assignment:{a.get('id')}",
                source="assignment",
            ))
            seen_assignment_ids.add(int(a["id"]))
    except requests.HTTPError as e:
        print(f"  ⚠ {course.code} assignments: {e}")

    # 2. Quizzes (only those NOT already represented as an assignment)
    try:
        for q in canvas.paginate(f"/courses/{cid}/quizzes"):
            if not q.get("published", True):
                continue
            # Quizzes that are graded show up as assignments too; skip duplicates
            if q.get("assignment_id") and int(q["assignment_id"]) in seen_assignment_ids:
                continue
            due = parse_iso(q.get("due_at"))
            name = q.get("title", "Untitled quiz")
            kind = "exam" if any(h in lower(name) for h in EXAM_HINTS) else "quiz"
            items.append(Item(
                week=week_number(due, semester_start, total_weeks),
                courseId=course.id,
                type=kind,
                title=name,
                detail=f"{q.get('points_possible') or 0:g} pts · {q.get('question_count', 0)} q",
                due=due_day_short(due),
                due_date=due.isoformat() if due else None,
                link=q.get("html_url", ""),
                points=q.get("points_possible"),
                canvas_id=f"quiz:{q.get('id')}",
                source="quiz",
            ))
    except requests.HTTPError as e:
        print(f"  ⚠ {course.code} quizzes: {e}")

    # 3. Discussions (only ungradeable ones — gradeable already counted as assignments)
    try:
        for d in canvas.paginate(f"/courses/{cid}/discussion_topics"):
            if not d.get("published", True):
                continue
            if d.get("assignment_id") and int(d["assignment_id"]) in seen_assignment_ids:
                continue
            due = parse_iso((d.get("assignment") or {}).get("due_at") or d.get("delayed_post_at"))
            items.append(Item(
                week=week_number(due, semester_start, total_weeks),
                courseId=course.id,
                type="discussion",
                title=d.get("title", "Untitled discussion"),
                detail="Ungraded discussion",
                due=due_day_short(due),
                due_date=due.isoformat() if due else None,
                link=d.get("html_url", ""),
                canvas_id=f"discussion:{d.get('id')}",
                source="discussion",
            ))
    except requests.HTTPError as e:
        print(f"  ⚠ {course.code} discussions: {e}")

    # 4. Module items (pages, files, external URLs — these become readings/videos)
    try:
        modules = list(canvas.paginate(f"/courses/{cid}/modules", {"include[]": "items"}))
        for module in modules:
            mod_week = guess_week_from_module_name(module.get("name", ""), semester_start, total_weeks)
            mod_items = module.get("items") or []
            for it in mod_items:
                kind = classify_module_item(it)
                if not kind:
                    continue
                items.append(Item(
                    week=mod_week,
                    courseId=course.id,
                    type=kind,
                    title=it.get("title", "Untitled"),
                    detail=f"Module: {module.get('name', '')}".strip(),
                    due="—",
                    link=it.get("html_url") or it.get("external_url") or "",
                    canvas_id=f"module_item:{it.get('id')}",
                    source="module_item",
                ))
    except requests.HTTPError as e:
        print(f"  ⚠ {course.code} modules: {e}")

    print(f"  ✓ {course.code}: {len(items)} items")
    return items


def points_detail(a: dict) -> str:
    parts = []
    pts = a.get("points_possible")
    if pts:
        parts.append(f"{pts:g} pts")
    sub_types = a.get("submission_types") or []
    pretty = {
        "online_upload": "file upload",
        "online_text_entry": "text entry",
        "online_url": "URL",
        "discussion_topic": "discussion",
        "online_quiz": "quiz",
        "media_recording": "media",
        "none": "no submission",
        "not_graded": "ungraded",
    }
    sub_str = ", ".join(pretty.get(s, s) for s in sub_types if s != "none")
    if sub_str:
        parts.append(sub_str)
    return " · ".join(parts)


def guess_week_from_module_name(name: str, semester_start: datetime, total_weeks: int) -> int:
    """Modules often named 'Week 3', 'Week 03 — Topic', 'Unit 2', 'Module 4 (Jun 8)', etc."""
    m = re.search(r"\b(?:week|wk|unit|module)\s*0*(\d+)", name, re.I)
    if m:
        n = int(m.group(1))
        return min(max(n, 1), total_weeks)
    # Try to find a date like "Jun 8" or "May 18"
    m = re.search(r"\b([A-Z][a-z]{2})\s+(\d{1,2})\b", name)
    if m:
        try:
            month = datetime.strptime(m.group(1), "%b").month
            day = int(m.group(2))
            year = semester_start.year
            d = datetime(year, month, day, tzinfo=timezone.utc)
            return week_number(d, semester_start, total_weeks)
        except ValueError:
            pass
    return 0  # unknown — bucketed as "unscheduled"


# ─────────────────────────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────────────────────────

def write_data(courses: list[Course], items: list[Item], cfg: dict) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "semester": cfg["semester"],
        "courses": [course_dict(c) for c in courses],
        "items": [asdict(i) for i in items],
        "totals": summarize(items),
    }
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_PATH.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    tmp.replace(DATA_PATH)
    print(f"\n✓ Wrote {DATA_PATH.relative_to(ROOT)} ({len(items)} items, {len(courses)} courses)")
    for k, v in payload["totals"].items():
        print(f"    {k:12} {v}")


def course_dict(c: Course) -> dict:
    return {
        "id": c.id,
        "canvas_id": c.canvas_id,
        "code": c.code,
        "name": c.name,
        "instructor": c.instructor,
        "color_bar": c.palette["bar"],
        "color_tag_bg": c.palette["tag_bg"],
        "color_tag_fg": c.palette["tag_fg"],
        "canvas_url": c.canvas_url,
    }


def summarize(items: list[Item]) -> dict:
    out = {t: 0 for t in ["reading", "video", "discussion", "paper", "assignment", "quiz", "exam"]}
    for i in items:
        out[i.type] = out.get(i.type, 0) + 1
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Entry
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    cfg = load_config()
    token = get_token(cfg)
    base_url = cfg["canvas_url"]
    canvas = Canvas(base_url, token)

    courses = select_courses(canvas, cfg)
    if not courses:
        raise SystemExit("❌ No active courses matched. Check 'canvas_term_name' in config.json.")

    all_items: list[Item] = []
    with ThreadPoolExecutor(max_workers=min(8, len(courses))) as pool:
        futures = {pool.submit(fetch_course_items, canvas, c, cfg): c for c in courses}
        for fut in as_completed(futures):
            try:
                all_items.extend(fut.result())
            except Exception as e:
                course = futures[fut]
                print(f"  ⚠ {course.code} failed: {e}", file=sys.stderr)

    # Stable sort: by week, then by due date, then by course, then by title
    all_items.sort(key=lambda i: (i.week or 99, i.due_date or "9999", i.courseId, i.title.lower()))

    write_data(courses, all_items, cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
