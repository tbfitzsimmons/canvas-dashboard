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

import hashlib

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

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
    type: str         # reading | video | lecture | discussion | paper | assignment | quiz | exam
    title: str
    detail: str = ""
    due: str = "—"    # short day-of-week e.g. "Thu"
    due_date: str | None = None  # ISO date for sorting
    link: str = ""
    points: float | None = None
    canvas_id: str = ""           # stable id from Canvas — used to preserve check-off state
    source: str = ""              # which Canvas object: assignment | quiz | discussion | page | module_item
    is_overview: bool = False     # weekly-summary page → pinned to top of its week column
    summary: str = ""             # short excerpt for overview rows (first ~180 chars of body text)


@dataclass
class Course:
    id: str           # internal id, e.g. "course1"
    canvas_id: int
    code: str
    name: str
    instructor: str
    zoom_url: str = ""  # Naropa's "Online Events" tab URL (Zoom integration)
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
    """Decide what an Assignment object represents.

    Precedence (most specific → least):
      1. discussion-typed submission        → discussion
      2. title says "discussion" + paper keyword IN TITLE + upload type → paper
         (rare: a paper assignment that happens to be titled "discussion …")
      3. title says "discussion"            → discussion
      4. exam hints / high-stakes final     → exam
      5. quiz submission                    → quiz
      6. paper hints in title or desc       → paper
      7. fallback                            → assignment
    """
    name = lower(a.get("name"))
    desc = lower(a.get("description") or "")
    points = a.get("points_possible") or 0
    submission_types = a.get("submission_types") or []
    title_has_discussion = "discussion" in name
    title_has_paper = any(h in name for h in PAPER_HINTS)

    # 1. Canvas-native graded discussion
    if "discussion_topic" in submission_types:
        return "discussion"

    # 2. Edge case: title says "discussion" but it's actually a paper upload
    if title_has_discussion and title_has_paper and (
        "online_upload" in submission_types or "online_text_entry" in submission_types
    ):
        return "paper"

    # 3. Title says "discussion" → trust the title
    if title_has_discussion:
        return "discussion"

    # 4. Exam check
    if any(h in name for h in EXAM_HINTS):
        return "exam"
    if points >= 100 and ("final" in name or "midterm" in name):
        return "exam"

    # 5. Quiz-as-assignment
    if "online_quiz" in submission_types:
        return "quiz"

    # 6. Paper check
    if "online_upload" in submission_types or "online_text_entry" in submission_types:
        if title_has_paper or any(h in desc for h in PAPER_HINTS[:3]):
            return "paper"

    return "assignment"


SECTION_DIVIDER_RE = re.compile(
    r"^(week|module|unit)\s*\d+\s*[:\-—]?\s*(assignments?|overview|index|home|materials?|resources?)?$",
    re.IGNORECASE,
)


def classify_module_item(item: dict, content: dict | None = None) -> str | None:
    """Decide what a ModuleItem (page/file/external_url) represents. Returns None to skip."""
    item_type = item.get("type", "")
    title = lower(item.get("title"))
    url = lower(item.get("external_url") or item.get("html_url") or "")

    if item_type in ("Assignment", "Quiz", "Discussion"):
        return None  # handled by their own endpoints, skip to avoid duplicates

    if item_type == "SubHeader":
        return None

    if SECTION_DIVIDER_RE.match((item.get("title") or "").strip()):
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
# Page-body expansion (Story 3+5)
# ─────────────────────────────────────────────────────────────────────────────
#
# Canvas Page module items often contain a flat list of readings or a bulleted
# list of videos. We fetch the page body HTML and extract one Item per
# reading/video so they each get their own checkbox on the dashboard.

OVERVIEW_TITLE_RE = re.compile(
    r"(overview|this\s*week.?s?\s*(topics|practice)|introduction\s+to\s+(the\s+)?week|"
    r"week\s+\d+\s+(overview|intro|introduction)|home|welcome)",
    re.IGNORECASE,
)

def _extract_summary(html: str, limit: int = 180) -> str:
    """Strip tags and return the first ~limit chars of visible text."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    text = _normalize_text(soup.get_text(separator=" "))
    if not text:
        return ""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # Trim back to last word boundary
    sp = cut.rfind(" ")
    if sp > 60:
        cut = cut[:sp]
    return cut + "…"


VIDEO_HOSTS = ("youtube.com", "youtu.be", "vimeo.com", "panopto", "kaltura", "zoom.us/rec")

# Cache resolved video titles for the run — many pages link the same video
_VIDEO_TITLE_CACHE: dict[str, str] = {}


def fetch_video_title(url: str) -> str | None:
    """For YouTube/Vimeo URLs, hit the free oEmbed endpoint to get the real
    video title. Returns None on failure. ~200ms per call, no auth required."""
    if not url:
        return None
    if url in _VIDEO_TITLE_CACHE:
        return _VIDEO_TITLE_CACHE[url] or None
    u = url.lower()
    try:
        if "youtube.com" in u or "youtu.be" in u:
            resp = requests.get(
                "https://www.youtube.com/oembed",
                params={"url": url, "format": "json"},
                timeout=10,
            )
        elif "vimeo.com" in u:
            resp = requests.get(
                "https://vimeo.com/api/oembed.json",
                params={"url": url},
                timeout=10,
            )
        else:
            return None
        if resp.status_code != 200:
            _VIDEO_TITLE_CACHE[url] = ""
            return None
        title = (resp.json() or {}).get("title")
        title = _normalize_text(title or "")
        _VIDEO_TITLE_CACHE[url] = title
        return title or None
    except Exception:
        _VIDEO_TITLE_CACHE[url] = ""
        return None

# Slide-deck / lecture-material hints — looked for in titles AND filenames.
LECTURE_HINTS_RE = re.compile(
    r"\b(slides?|powerpoint|ppt|pptx|keynote|deck|lecture\s+notes?)\b",
    re.IGNORECASE,
)

# Section headings that mean "what follows is a deliverable to track"
DELIVERABLE_SECTIONS = re.compile(
    r"^(readings?|required reading|assigned reading|recommended|videos?|"
    r"lectures?|resources?|files?|slides?|materials?)\b",
    re.IGNORECASE,
)
# Sections to drop — class discussion prompts, agenda, etc.
IGNORE_SECTIONS = re.compile(
    r"^(discussion (questions?|topics?|prompts?)|class topics?|agenda|schedule|notes?)\b",
    re.IGNORECASE,
)

# Strip URLs out of a text fragment so we can use the surrounding words as title
URL_IN_TEXT_RE = re.compile(r"https?://\S+")


def _normalize_text(s: str) -> str:
    """Collapse whitespace, strip NBSP, trim."""
    return re.sub(r"\s+", " ", (s or "").replace(" ", " ")).strip()


def _looks_like_heading(p: Tag) -> bool:
    """A <p> ending in ':', short, with no <a>, behaves like a section heading."""
    if p.find("a"):
        return False
    text = _normalize_text(p.get_text())
    return bool(text) and len(text) <= 80 and text.endswith(":")


def _classify_link_type(href: str, title: str = "") -> str:
    h = (href or "").lower()
    if any(host in h for host in VIDEO_HOSTS):
        return "video"
    # Slide decks / PowerPoint files → lecture (check both filename and visible title)
    if LECTURE_HINTS_RE.search(h) or LECTURE_HINTS_RE.search(title or ""):
        return "lecture"
    return "reading"


def _title_from_li_with_link(li: Tag, a: Tag) -> str:
    """For an <li> like 'Thomas Szaz <a>https://youtu.be/...</a>',
    prefer the <li>'s text minus the URL; fall back to <a>'s text."""
    a_text = _normalize_text(a.get_text())
    li_text = _normalize_text(li.get_text())
    # If <a>'s visible text IS the URL, use the surrounding li text
    if a_text.startswith("http"):
        without_url = URL_IN_TEXT_RE.sub("", li_text).strip(" -–—:•")
        if without_url:
            return _normalize_text(without_url)
    return a_text or li_text or "Untitled link"


def fetch_page_body(canvas: Canvas, course_canvas_id: int, page_url: str) -> str | None:
    """Fetch a Canvas Page's HTML body. Returns None on 404/error."""
    try:
        resp = canvas._get(f"/courses/{course_canvas_id}/pages/{page_url}")
        return (resp.json() or {}).get("body")
    except requests.HTTPError as e:
        if getattr(e.response, "status_code", None) == 404:
            return None
        print(f"  ⚠ page fetch {page_url}: {e}")
        return None


def _looks_like_prose(text: str) -> bool:
    """A reading title is a noun phrase. Reject anything that looks like a
    sentence fragment, greeting, or instruction."""
    if not text:
        return True
    if len(text) < 4 or len(text) > 200:
        return True
    # Reading titles don't end with sentence punctuation
    if text[-1] in ".!?,;":
        return True
    # Greetings, instructions
    if re.match(
        r"^(hi|hello|hey|dear|welcome|please|note|reminder|important|"
        r"if you|when you|you (will|should|can|may)|in studio|"
        r"based on|why is|how (do|does|can|could)|what (is|are|do|does))\b",
        text,
        re.IGNORECASE,
    ):
        return True
    # Multiple sentences (mid-text period followed by space + capital) → prose
    if re.search(r"[a-z]\. [A-Z]", text):
        return True
    return False


def _dedupe_key(text: str) -> str:
    """Normalize a title for dedupe: lowercase, drop all non-alphanumerics."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def expand_page_body(html: str, page_title: str = "") -> list[tuple[str, str, str]]:
    """Parse a Canvas page body and return [(type, title, link), ...].

    Walks the DOM top-to-bottom, tracking the current section (most recent
    heading-like text). Plain-text items (no <a> link) only emit when we're
    inside a "deliverable" section heading (Readings, Videos, Resources, etc.)
    OR when the page title itself is deliverable (e.g. "Readings and
    Presentation for Week 1" — then the whole page is one big deliverable
    section). Linked items emit regardless of section context.
    """
    if not html or not html.strip():
        return []
    soup = BeautifulSoup(html, "html.parser")
    out: list[tuple[str, str, str]] = []
    # If the page title itself names a deliverable section, treat the whole
    # page as if it were under that heading (handles "Readings and Presentation
    # for Week 1" — no internal headings, just paragraphs of readings).
    page_title_is_deliverable = bool(
        page_title and DELIVERABLE_SECTIONS.search(page_title)
    )
    current_section = page_title if page_title_is_deliverable else ""

    def section_is_ignore() -> bool:
        return bool(current_section) and bool(IGNORE_SECTIONS.match(current_section))

    def section_is_deliverable() -> bool:
        return bool(current_section) and bool(DELIVERABLE_SECTIONS.search(current_section))

    def emit_link(a: Tag, fallback_title: str = "") -> None:
        href = a.get("href") or ""
        if not href or href.startswith("#"):
            return
        title = _normalize_text(a.get_text()) or fallback_title or href
        # Use the <a>'s "title" attribute for file links (Canvas puts the filename there)
        if a.get("title") and (not title or title.startswith("http")):
            title = _normalize_text(a["title"])
        if title.startswith("http"):
            # raw URL as text — try the surrounding <li> text
            li = a.find_parent("li")
            if li:
                surround = URL_IN_TEXT_RE.sub("", _normalize_text(li.get_text())).strip(" -–—:•")
                if surround:
                    title = surround
        # Still a bare URL? For YouTube/Vimeo, hit oEmbed for the real title.
        if title.startswith("http"):
            resolved = fetch_video_title(href)
            if resolved:
                title = resolved
        out.append((_classify_link_type(href, title), title, href))

    # Iterate top-level children, but also recurse into <ul>/<ol>
    def walk(nodes: Iterable) -> None:
        nonlocal current_section
        for node in nodes:
            if isinstance(node, NavigableString):
                continue
            if not isinstance(node, Tag):
                continue
            name = node.name.lower()

            # Headings
            if name in ("h1", "h2", "h3", "h4"):
                # An <h2> can wrap a single file link — emit that link, treat heading as new section
                links = node.find_all("a")
                if links:
                    for a in links:
                        if not section_is_ignore():
                            emit_link(a)
                current_section = _normalize_text(node.get_text())
                continue

            if name == "p":
                if _looks_like_heading(node):
                    current_section = _normalize_text(node.get_text())
                    continue
                if section_is_ignore():
                    continue
                links = node.find_all("a")
                if links:
                    for a in links:
                        emit_link(a)
                else:
                    text = _normalize_text(node.get_text())
                    if section_is_deliverable() and not _looks_like_prose(text):
                        kind = "lecture" if LECTURE_HINTS_RE.search(text) else "reading"
                        out.append((kind, text, ""))
                continue

            if name in ("ul", "ol"):
                for li in node.find_all("li", recursive=False):
                    if section_is_ignore():
                        continue
                    links = li.find_all("a")
                    if links:
                        for a in links:
                            emit_link(a, fallback_title=_title_from_li_with_link(li, a))
                    else:
                        text = _normalize_text(li.get_text())
                        # Treat li ending in ':' as a sub-heading (skip, but don't reset section)
                        if not text or text.endswith(":"):
                            continue
                        if section_is_deliverable() and not _looks_like_prose(text):
                            kind = "lecture" if LECTURE_HINTS_RE.search(text) else "reading"
                            out.append((kind, text, ""))
                continue

            if name == "a":
                if not section_is_ignore():
                    emit_link(node)
                continue

            # Recurse for <div>, <span>, etc.
            if node.contents:
                walk(node.contents)

    walk(soup.children)

    # Drop blanks and obvious noise
    cleaned = []
    seen_in_page = set()
    for typ, title, link in out:
        t = _normalize_text(title)
        if not t or t in {" ", "•", "-"}:
            continue
        if t.endswith(":") and len(t) <= 80:  # leftover heading caught somewhere
            continue
        key = t.lower()
        if key in seen_in_page:
            continue
        seen_in_page.add(key)
        cleaned.append((typ, t, link))
    return cleaned


def page_child_id(page_id: int | str, title: str) -> str:
    """Stable id for a page-derived item, based on page id + title hash."""
    h = hashlib.md5(_normalize_text(title).lower().encode("utf-8")).hexdigest()[:8]
    return f"page_child:{page_id}:{h}"


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

    instructor_overrides = cfg.get("instructor_overrides", {})
    courses = []
    for i, c in enumerate(raw):
        teachers = c.get("teachers") or []
        instructor = teachers[0].get("display_name") if teachers else ""
        # Allow config to override wrong/multiple-teacher courses
        course_code_base = c.get("course_code", "").split(".")[0]
        instructor = instructor_overrides.get(course_code_base, instructor)
        zoom_url = _find_zoom_tab_url(canvas, int(c["id"]))
        courses.append(Course(
            id=f"course{i+1}",
            canvas_id=int(c["id"]),
            code=c.get("course_code", "").split(".")[0],  # strip ".2026SU" suffix
            name=clean_course_name(c.get("name", "")),
            instructor=instructor,
            palette=PALETTE[i % len(PALETTE)],
            canvas_url=f"{canvas.base}/courses/{c['id']}",
            zoom_url=zoom_url,
        ))
    print(f"  ✓ {len(courses)} active course(s): {', '.join(c.code for c in courses)}")
    return courses


def _find_zoom_tab_url(canvas: Canvas, course_id: int) -> str:
    """Find the Zoom/conferencing URL for a course.

    Three-pass search (fastest → most expensive):
      1. Canvas navigation tab labelled 'Online Events', 'Zoom', 'Conferences', etc.
      2. Course front page HTML — scan for zoom.us meeting links.
      3. Canvas Pages whose title contains 'instructor', 'contact', 'professor',
         'syllabus', or 'zoom' — scan each for a meeting link.
    """
    # ── 1. Navigation tab ────────────────────────────────────────────────────
    try:
        tabs = canvas._get(f"/courses/{course_id}/tabs").json() or []
    except requests.HTTPError:
        tabs = []
    tab_needles = ("online events", "zoom", "conferences", "meetings", "video conferencing")
    for tab in tabs:
        label = (tab.get("label") or "").lower()
        if any(n in label for n in tab_needles):
            url = tab.get("full_url") or tab.get("html_url") or ""
            if url and not url.startswith("http"):
                url = canvas.base + url
            # Only accept if it's a real Zoom meeting URL.
            # Canvas LTI tab URLs (naropa.instructure.com/…/external_tools/…)
            # open the Zoom scheduler inside Canvas — not a direct meeting link.
            if "zoom.us" in url:
                return url
            # Tab found but not a direct link — fall through to HTML passes.

    # ── 2. Course front page ──────────────────────────────────────────────────
    try:
        front = canvas._get(f"/courses/{course_id}/front_page").json()
        url = _extract_zoom_url_from_html(front.get("body", ""))
        if url:
            return url
    except requests.HTTPError:
        pass

    # ── 3. Instructor / syllabus pages ────────────────────────────────────────
    page_needles = ("instructor", "professor", "contact", "syllabus", "zoom", "faculty")
    try:
        pages = canvas._get(f"/courses/{course_id}/pages",
                            params={"per_page": 50, "sort": "title"}).json() or []
        for page in pages:
            title = (page.get("title") or "").lower()
            if any(n in title for n in page_needles):
                body = fetch_page_body(canvas, course_id, page["url"])
                url = _extract_zoom_url_from_html(body or "")
                if url:
                    return url
    except requests.HTTPError:
        pass

    return ""


def _extract_zoom_url_from_html(html: str) -> str:
    """Return the first zoom.us meeting/webinar link found in HTML, or ''."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "zoom.us" in href and any(seg in href for seg in ("/j/", "/my/", "/meeting/", "/s/", "/wc/")):
            return href
    # Plain-text URL not wrapped in <a>
    m = re.search(r"https?://[a-z0-9.\-]*zoom\.us/(?:j|my|meeting|s|wc)/[^\s\"'<>]+", html)
    return m.group(0).rstrip(".,;)\"'") if m else ""


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

    # Pre-fetch modules once — reused for (a) discussion week fallback and
    # (b) the full module-item pass below.  Avoids a double API round-trip.
    try:
        modules_data = list(canvas.paginate(f"/courses/{cid}/modules", {"include[]": "items"}))
    except requests.HTTPError:
        modules_data = []

    # Build discussion_id → module_week map so ungraded discussions with no
    # due_at can still be placed in the right week column.
    discussion_module_weeks: dict[str, int] = {}
    for _mod in modules_data:
        _mw = guess_week_from_module_name(_mod.get("name", ""), semester_start, total_weeks)
        for _it in _mod.get("items") or []:
            if _it.get("type") == "Discussion" and _it.get("content_id"):
                discussion_module_weeks[str(_it["content_id"])] = _mw

    # 1. Assignments
    try:
        for a in canvas.paginate(f"/courses/{cid}/assignments"):
            if not a.get("published", True):
                continue
            # Use due_at if set; fall back to unlock_at (when it becomes available).
            # lock_at is intentionally not used — it's the cutoff, not the deadline.
            due = parse_iso(a.get("due_at")) or parse_iso(a.get("unlock_at"))
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
        if getattr(e.response, "status_code", None) == 404:
            print(f"  ℹ {course.code}: quizzes endpoint disabled (404)")
        else:
            print(f"  ⚠ {course.code} quizzes: {e}")

    # 3. Discussions (only ungradeable ones — gradeable already counted as assignments)
    try:
        for d in canvas.paginate(f"/courses/{cid}/discussion_topics"):
            if not d.get("published", True):
                continue
            if d.get("assignment_id") and int(d["assignment_id"]) in seen_assignment_ids:
                continue
            due = parse_iso((d.get("assignment") or {}).get("due_at") or d.get("delayed_post_at"))
            # Fallback: use the week inferred from whichever module this discussion
            # lives in — catches discussions that have no due date of their own.
            mod_week_fallback = discussion_module_weeks.get(str(d.get("id", "")), 0)
            wk = week_number(due, semester_start, total_weeks) if due else mod_week_fallback
            d_title = d.get("title", "Untitled discussion")
            d_title_lc = lower(d_title)
            video_hints = ("recording", "video", "zoom recording", "lecture video", "recorded session")
            d_type = "video" if any(h in d_title_lc for h in video_hints) else "discussion"
            items.append(Item(
                week=wk,
                courseId=course.id,
                type=d_type,
                title=d_title,
                detail="" if d_type == "video" else "Ungraded discussion",
                due=due_day_short(due),
                due_date=due.isoformat() if due else None,
                link=d.get("html_url", ""),
                canvas_id=f"discussion:{d.get('id')}",
                source="discussion",
            ))
    except requests.HTTPError as e:
        if getattr(e.response, "status_code", None) == 404:
            print(f"  ℹ {course.code}: discussions endpoint disabled (404)")
        else:
            print(f"  ⚠ {course.code} discussions: {e}")

    # 4. Module items (pages, files, external URLs — these become readings/videos)
    page_children_emitted = 0
    pages_expanded = 0
    seen_module_page_urls: set[str] = set()  # track for coverage check
    try:
        modules = modules_data  # already fetched above; no second API call needed
        # Dedupe page-children across the whole course by normalized title
        seen_page_child_titles: set[str] = set()
        for module in modules:
            mod_week = guess_week_from_module_name(module.get("name", ""), semester_start, total_weeks)
            mod_items = module.get("items") or []
            for it in mod_items:
                kind = classify_module_item(it)
                if not kind:
                    continue

                base_link = it.get("html_url") or it.get("external_url") or ""
                title = it.get("title", "Untitled")

                # Story 4: detect weekly-overview pages — keep as single pinned row,
                # don't expand them into children.
                is_overview_page = (
                    it.get("type") == "Page"
                    and bool(OVERVIEW_TITLE_RE.search(title or ""))
                )

                # Story 3+5: if this is a Page (and NOT an overview), fetch body and expand
                children: list[tuple[str, str, str]] = []
                body_for_summary: str | None = None
                if it.get("type") == "Page" and it.get("page_url"):
                    seen_module_page_urls.add(it["page_url"])
                    body_for_summary = fetch_page_body(canvas, course.canvas_id, it["page_url"])
                    if not is_overview_page:
                        children = expand_page_body(body_for_summary or "", page_title=title)

                if children:
                    pages_expanded += 1
                    page_id = it.get("id")
                    for c_type, c_title, c_link in children:
                        key = _dedupe_key(c_title)
                        if not key or key in seen_page_child_titles:
                            continue
                        seen_page_child_titles.add(key)
                        items.append(Item(
                            week=mod_week,
                            courseId=course.id,
                            type=c_type,
                            title=c_title,
                            detail=f"from: {it.get('title', '')}".strip(),
                            due="—",
                            link=c_link or base_link,
                            canvas_id=page_child_id(page_id, c_title),
                            source="page_child",
                        ))
                        page_children_emitted += 1
                else:
                    items.append(Item(
                        week=mod_week,
                        courseId=course.id,
                        type=kind,
                        title=title,
                        detail="",
                        due="—",
                        link=base_link,
                        canvas_id=f"module_item:{it.get('id')}",
                        source="module_item",
                        is_overview=is_overview_page,
                        summary=_extract_summary(body_for_summary or "") if is_overview_page else "",
                    ))
        if pages_expanded:
            print(f"  ↳ {course.code}: expanded {pages_expanded} page(s) into {page_children_emitted} child items")
    except requests.HTTPError as e:
        print(f"  ⚠ {course.code} module page expansion: {e}")

    print(f"  ✓ {course.code}: {len(items)} items")
    _coverage_check(canvas, course, items, seen_module_page_urls)
    return items


def _coverage_check(
    canvas: Canvas,
    course: Course,
    items: list[Item],
    seen_module_page_urls: set[str],
) -> None:
    """Print a per-course coverage sanity check to the Actions log.

    Flags:
    - Graded assignments with no due date (will show in Undated section)
    - unlock_at as a fallback signal for undated items
    - Canvas pages that exist but are not referenced by any module
    """
    from collections import Counter
    cid = course.canvas_id
    by_source = Counter(it.source for it in items)

    undated_graded = [
        it for it in items
        if it.source == "assignment" and it.week == 0
    ]

    lines = [
        f"  📊 {course.code}: "
        f"assignments={by_source.get('assignment', 0)}, "
        f"quizzes={by_source.get('quiz', 0)}, "
        f"discussions={by_source.get('discussion', 0)}, "
        f"readings/videos={by_source.get('module_item', 0) + by_source.get('page_child', 0)}, "
        f"total={len(items)}"
    ]

    if undated_graded:
        titles = ", ".join(f'"{it.title}"' for it in undated_graded[:3])
        extra = f" +{len(undated_graded) - 3} more" if len(undated_graded) > 3 else ""
        lines.append(
            f"  ⚠  {course.code}: {len(undated_graded)} graded assignment(s) have no due date "
            f"(shown in Undated section): {titles}{extra}"
        )

    # Check for Canvas pages not referenced by any module
    try:
        all_pages = list(canvas.paginate(f"/courses/{cid}/pages", {"per_page": 50}))
        orphan_pages = [
            p for p in all_pages
            if p.get("url") and p["url"] not in seen_module_page_urls
            and p.get("published", True)
        ]
        if orphan_pages:
            titles = ", ".join(f'"{p.get("title", "?")}"' for p in orphan_pages[:4])
            extra = f" +{len(orphan_pages) - 4} more" if len(orphan_pages) > 4 else ""
            lines.append(
                f"  ⚠  {course.code}: {len(orphan_pages)} published page(s) not in any module "
                f"(may be missed): {titles}{extra}"
            )
    except requests.HTTPError:
        pass

    for line in lines:
        print(line)


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
        "token_expires": cfg.get("token_expires"),
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
    # Story 2: how many assignment-source items ended up as discussion
    reclassified = sum(1 for i in items if i.source == "assignment" and i.type == "discussion")
    print(f"    (reclassified to discussion from assignment source: {reclassified})")


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
        "zoom_url": c.zoom_url,
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
