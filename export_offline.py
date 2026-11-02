#!/usr/bin/env python3
"""
export_offline.py — emit ONE self-contained HTML copy of the dashboard.

WHY THIS EXISTS (backlog item 3)
--------------------------------
Jennifer's coursework (6.7 GB, 4,272 files) lives in her PERSONAL Google Drive
and survives graduation. The thing that makes it navigable — this board — lives
on GitHub Pages, built from tbfitzsimmons/canvas-dashboard. If that repo ever
lapses, six semesters of index vanish while the content sits in Drive with no
way to browse it. The navigation layer is the fragile part, and it is the part
nobody else builds: canvas-student-data-export (276*) emits raw JSON and HTML
with no navigation at all.

So: one file, openable from file://, no network, nothing to maintain.

HOW IT WORKS
------------
The live board fetches its data at runtime (`boot`, `loadArchiveIndex`,
`loadVideoNotes`, `loadReadingNotes`, `loadSemester`, `pollForFreshData`). Under
file:// those fetches fail outright — Chrome treats a file:// page as an opaque
origin, so the live index.html is not merely stale offline, it is blank.

This script does NOT rewrite the board's JavaScript. Editing 1,400 lines of
someone else's rendering code is how an exporter rots the first time the board
changes — and a sibling session is editing dashboard/index.html right now. It
injects two blocks around the untouched board instead:

  1. a PRELUDE before the board's <script>, holding every JSON payload inline
     plus an offline resolver installed as `window.fetch`;
  2. an EPILOGUE after it, which neutralises the sync/refresh paths and renders
     an honest banner.

The resolver never holds a reference to the native fetch, so after the prelude
runs there is no code path from the page to the network at all. Blocked attempts
are recorded and surfaced IN THE PAGE rather than swallowed.

TITLE AND LINK ONLY
-------------------
Archived boards carry zero body text: `summary` is stripped by
`strip_archived_content()` in sync.py before an archive file is written, because
dashboard/archive/*.json is served publicly and prior-term material includes
publisher-licensed readings. This exporter inlines those files verbatim and
re-asserts the invariant on the way out (`assert_title_and_link_only`). It never
merges current-semester text into an archived board.

USAGE
-----
    python3 export_offline.py                  # -> canvas-dashboard-offline.html
    python3 export_offline.py -o /tmp/x.html
    python3 export_offline.py --self-test      # prove the checks can fail
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
DASH = REPO / "dashboard"
BOARD = DASH / "index.html"
ARCHIVE_DIR = DASH / "archive"

DEFAULT_OUT = REPO / "canvas-dashboard-offline.html"

# Payloads the board fetches by relative path. `required` ones are a hard
# failure; the optional ones are legitimately absent on some deployments
# (video-notes.json and reading-notes.json 404 on the live board today and it
# renders unchanged), so their absence is reported in the page, not fatal.
CORE_PAYLOADS = [
    ("data.json", DASH / "data.json", True),
    ("archive/index.json", ARCHIVE_DIR / "index.json", False),
    ("video-notes.json", DASH / "video-notes.json", False),
    ("reading-notes.json", DASH / "reading-notes.json", False),
]


# ─────────────────────────────────────────────────────────────── collection ──

def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def collect() -> tuple[dict, list[str], dict]:
    """Gather every payload. Returns (bundle, degradations, stats).

    `degradations` is the honest list: anything that could not be inlined ends
    up here and is printed into the exported page.
    """
    if not BOARD.is_file():
        raise SystemExit(f"FATAL: board not found at {BOARD}")

    bundle: dict[str, object] = {}
    degraded: list[str] = []

    for key, path, required in CORE_PAYLOADS:
        if not path.is_file():
            msg = f"{key} is not present in the repo and could not be inlined."
            if required:
                raise SystemExit(f"FATAL: {msg}")
            degraded.append(msg)
            continue
        try:
            bundle[key] = load_json(path)
        except Exception as e:  # malformed JSON must not produce a silent gap
            msg = f"{key} could not be parsed ({type(e).__name__}) and was not inlined."
            if required:
                raise SystemExit(f"FATAL: {msg}")
            degraded.append(msg)

    # Archived semesters. The index is the source of truth for what the picker
    # offers, so walk it rather than globbing: a file on disk that the index
    # does not list is not reachable from the UI, and an index entry with no
    # file on disk is exactly the gap that has to be declared.
    index = bundle.get("archive/index.json") or []
    semesters_inlined = []
    for entry in index:
        rel = entry.get("file")  # e.g. "archive/summer-2026.json"
        name = entry.get("name") or entry.get("slug") or "(unnamed)"
        if not rel:
            degraded.append(f"Archived semester {name} has no file path in the index.")
            continue
        path = DASH / rel
        if not path.is_file():
            degraded.append(
                f"Archived semester {name} is listed in the index but "
                f"{rel} is missing from the repo — it is NOT available offline."
            )
            continue
        try:
            bundle[rel] = load_json(path)
        except Exception as e:
            degraded.append(
                f"Archived semester {name} ({rel}) could not be parsed "
                f"({type(e).__name__}) — it is NOT available offline."
            )
            continue
        semesters_inlined.append((name, rel, len(bundle[rel].get("items") or [])))

    # Any archive file on disk the index never mentions
    if ARCHIVE_DIR.is_dir():
        listed = {e.get("file") for e in index}
        for f in sorted(ARCHIVE_DIR.glob("*.json")):
            rel = f"archive/{f.name}"
            if f.name != "index.json" and rel not in listed:
                degraded.append(
                    f"{rel} exists in the repo but is not listed in "
                    f"archive/index.json, so the semester picker cannot reach it."
                )

    cur = bundle["data.json"]
    stats = {
        "current_name": (cur.get("semester") or {}).get("name") or "Current",
        "current_items": len(cur.get("items") or []),
        "current_courses": len(cur.get("courses") or []),
        "current_generated_at": cur.get("generated_at"),
        "archived": semesters_inlined,
        "archived_items": sum(n for _, _, n in semesters_inlined),
        "video_notes": _count_notes(bundle.get("video-notes.json")),
        "reading_notes": _count_notes(bundle.get("reading-notes.json")),
    }
    stats["semesters"] = 1 + len(semesters_inlined)
    stats["items"] = stats["current_items"] + stats["archived_items"]
    return bundle, degraded, stats


def _count_notes(blob) -> int:
    if not isinstance(blob, dict):
        return 0
    notes = blob.get("notes") if isinstance(blob.get("notes"), dict) else blob
    return len(notes or {})


# ────────────────────────────────────────────────────────────────── guards ──

def assert_title_and_link_only(bundle: dict) -> list[str]:
    """Re-assert the standing constraint on the way out.

    Every payload whose own `archived` flag is true must carry zero body text.
    Returns the list of offending "<file> :: <title>" strings — empty is a pass.
    This deliberately keys off the data's OWN archived marker rather than a
    filename pattern, so a future archive written anywhere is still checked.
    """
    offenders = []
    for key, blob in bundle.items():
        if not isinstance(blob, dict) or not blob.get("archived"):
            continue
        for it in blob.get("items") or []:
            if (it.get("summary") or "").strip():
                offenders.append(f"{key} :: {it.get('title', '?')!r}")
    return offenders


# ── external subresources ───────────────────────────────────────────────────
#
# Caught only by opening the export in a real browser: the board's <head> pulls
# Inter and JetBrains Mono from fonts.googleapis.com / fonts.gstatic.com. The
# JSON was inlined but those three <link> tags were not, so the "no network"
# claim was false — a file:// load fired four off-box requests and fell back to
# system fonts silently. Source review could not see it; the network panel could.
#
# They are stripped rather than embedded: --font already falls back to
# "system-ui, -apple-system, sans-serif" and --font-mono to "ui-monospace,
# monospace", so removing the links costs a little typographic polish and
# nothing else. Embedding the woff2 files instead would add ~300 KB and make
# this exporter need network access to run, which is the wrong dependency for a
# tool whose whole purpose is surviving the loss of infrastructure.

REMOTE_LINK_RE = re.compile(
    r'[ \t]*<link\b[^>]*\bhref\s*=\s*["\']https?://[^>]*>[ \t]*\n?', re.I)

REMOTE_SUBRESOURCE_PATTERNS = [
    ("stylesheet/link", re.compile(r'<link\b[^>]*href\s*=\s*["\']https?://', re.I)),
    ("script src", re.compile(r'<script\b[^>]*src\s*=\s*["\']https?://', re.I)),
    ("embedded media", re.compile(
        r'<(?:img|iframe|source|video|audio|embed|object)\b[^>]*'
        r'(?:src|data)\s*=\s*["\']https?://', re.I)),
    ("css @import", re.compile(r'@import\s+(?:url\()?\s*["\']?https?://', re.I)),
    ("css url()", re.compile(r'url\(\s*["\']?https?://', re.I)),
]


def strip_remote_subresources(html: str) -> tuple[str, list[str]]:
    """Remove <link> tags pointing off-box and declare the consequence.

    Returns (html, degradations).
    """
    found = REMOTE_LINK_RE.findall(html)
    notes: list[str] = []
    if found:
        html = REMOTE_LINK_RE.sub("", html)
        hosts = sorted({
            re.search(r'https?://([^/"\']+)', f).group(1) for f in found
        })
        notes.append(
            "Web fonts (Inter, JetBrains Mono) were NOT inlined: "
            f"{len(found)} stylesheet/preconnect link(s) to {', '.join(hosts)} "
            "were removed so this file makes no network requests. The board "
            "falls back to your system fonts, so text may be set slightly "
            "differently than on the live board. Nothing else changes."
        )
    return html, notes


def find_remote_subresources(html: str) -> list[str]:
    """Anything left in the export that would still reach off-box at load time.
    Clickable <a href> links and URLs inside the data bundle are deliberately
    NOT matched — those are destinations the user chooses, not loads the page
    performs."""
    hits = []
    for label, rx in REMOTE_SUBRESOURCE_PATTERNS:
        for m in rx.finditer(html):
            hits.append(f"{label}: {html[m.start():m.start() + 110]!r}")
    return hits


def archived_payload_count(bundle: dict) -> int:
    """How many payloads the guard above actually examined. A guard that
    inspected nothing is not a guard, so the caller prints this next to the
    zero it reports."""
    return sum(
        1 for b in bundle.values() if isinstance(b, dict) and b.get("archived")
    )


# ──────────────────────────────────────────────────────────── JS embedding ──

def js_json(obj) -> str:
    """Serialise for embedding inside an HTML <script> block.

    `<`, `>` and `&` are escaped to their \\u form so no payload string can
    close the script element or be read as markup; U+2028/U+2029 are escaped
    because they are literal line terminators in JS but legal raw in JSON.
    None of these characters occurs in JSON's structural syntax, so a blanket
    replacement is safe.
    """
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return (
        s.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


OFFLINE_RUNTIME = r"""
/* ═══════════════════════════════════════════════════════════════════════════
   OFFLINE RUNTIME — injected by export_offline.py. Not part of the live board.

   Installed BEFORE the board's own script. Replaces window.fetch with a
   resolver over the inlined bundle and keeps NO reference to the native fetch,
   so from here on the page has no code path to the network. Anything the board
   asks for that is not inlined is refused loudly and listed in the page.
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var BUNDLE = {};
  try {
    BUNDLE = JSON.parse(document.getElementById('cd-offline-bundle').textContent);
  } catch (e) {
    document.addEventListener('DOMContentLoaded', function () {
      var b = document.getElementById('cd-offline-banner');
      if (b) b.insertAdjacentHTML('beforeend',
        '<p class="cd-off-bad">The inlined data block in this file is corrupt (' +
        String(e && e.message) + '). This copy cannot render. Keep the file and ' +
        're-export from the repo if it still exists.</p>');
    });
  }

  var OFF = window.__CD_OFFLINE__ = {
    bundle: BUNDLE,
    served: [],   // keys resolved from the bundle
    blocked: [],  // anything that wanted the network
  };

  function note(list, what) {
    if (list.indexOf(what) === -1) list.push(what);
    if (list === OFF.blocked) renderBlocked();
  }

  // Deep copy on every hand-out: the board mutates what it gets back
  // (loadSemester assigns archived.archives), and a shared object would let one
  // view's edits leak into the next.
  var clone = (typeof structuredClone === 'function')
    ? structuredClone
    : function (o) { return JSON.parse(JSON.stringify(o)); };

  function keyFor(input) {
    var u = (typeof input === 'string') ? input
          : (input && input.url) ? input.url : String(input);
    u = u.split('#')[0].split('?')[0];      // drop the ?t= cache-buster
    if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(u)) return null;  // absolute → off-box
    return u.replace(/^\.\//, '').replace(/^\//, '');
  }

  function response(key, value) {
    return {
      ok: true, status: 200, statusText: 'OK (inlined)',
      url: key, redirected: false, type: 'basic', bodyUsed: false,
      headers: { get: function () { return null; } },
      json: function () { return Promise.resolve(clone(value)); },
      text: function () { return Promise.resolve(JSON.stringify(value)); },
      clone: function () { return response(key, value); },
    };
  }

  window.fetch = function (input, init) {
    var key = keyFor(input);
    if (key !== null && Object.prototype.hasOwnProperty.call(BUNDLE, key)) {
      note(OFF.served, key);
      return Promise.resolve(response(key, BUNDLE[key]));
    }
    var target = (typeof input === 'string') ? input
               : (input && input.url) ? input.url : String(input);
    var method = (init && init.method) || 'GET';
    note(OFF.blocked, method + ' ' + target);
    console.warn('[offline] refused (nothing is fetched in this copy):', method, target);
    return Promise.reject(new Error(
      'Offline copy: ' + target + ' is not available. This file contains no ' +
      'network access.'));
  };

  // The board does not use these, but leaving a live escape hatch would make
  // the "no network" claim depend on the board never changing.
  if (window.XMLHttpRequest) {
    var XHR = window.XMLHttpRequest;
    window.XMLHttpRequest = function () {
      var x = new XHR();
      x.open = function (m, u) {
        note(OFF.blocked, 'XHR ' + m + ' ' + u);
        throw new Error('Offline copy: XMLHttpRequest is disabled.');
      };
      return x;
    };
  }
  if (navigator.sendBeacon) {
    navigator.sendBeacon = function (u) { note(OFF.blocked, 'beacon ' + u); return false; };
  }
  if (window.WebSocket) {
    window.WebSocket = function (u) {
      note(OFF.blocked, 'websocket ' + u);
      throw new Error('Offline copy: WebSocket is disabled.');
    };
  }
  if (window.EventSource) {
    window.EventSource = function (u) {
      note(OFF.blocked, 'eventsource ' + u);
      throw new Error('Offline copy: EventSource is disabled.');
    };
  }

  // Degrade visibly, not silently. If anything in this copy ever reaches for
  // the network, it says so on the page instead of failing quietly.
  function renderBlocked() {
    var el = document.getElementById('cd-offline-blocked');
    if (!el) return;
    if (!OFF.blocked.length) { el.hidden = true; return; }
    el.hidden = false;
    el.innerHTML = '<strong>Blocked while you were using this copy ' +
      '(' + OFF.blocked.length + '):</strong> these need the network and did ' +
      'nothing. <code>' +
      OFF.blocked.map(function (s) {
        return s.replace(/[&<>"]/g, function (c) {
          return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c];
        });
      }).join('</code> <code>') + '</code>';
  }
  OFF.renderBlocked = renderBlocked;
})();
"""


OFFLINE_EPILOGUE = r"""
/* ═══════════════════════════════════════════════════════════════════════════
   OFFLINE EPILOGUE — injected by export_offline.py.

   Runs after the board's script, so these overrides win over its declarations.
   Each of these paths exists only to talk to the Cloudflare worker or the
   GitHub API. Neither is reachable here. The fetch resolver above would already
   refuse them; replacing them outright means the attempt is never even made,
   and the user gets a sentence instead of a console error.
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';
  var OFF = window.__CD_OFFLINE__ || {};

  window.pullRemote = function () { return Promise.resolve(); };
  window.flushRemote = function () { return Promise.resolve(); };
  window.pollForFreshData = function () {};

  window.triggerRefresh = function () {
    var btn = document.getElementById('refresh-btn');
    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Offline copy — cannot sync';
    }
    alert(
      'This is the offline copy.\n\n' +
      'Refresh triggers a Canvas sync through a Cloudflare worker and GitHub ' +
      'Actions. Neither is reachable from a file on disk, and after graduation ' +
      'neither will exist.\n\n' +
      'Everything already in this file is complete and will keep working.'
    );
  };

  // The sync status pill advertises cross-device sync. There is none here.
  if (typeof window.setSyncStatus === 'function') {
    window.setSyncStatus = function () {
      var el = document.getElementById('sync-status');
      if (el) { el.textContent = 'local only'; el.title = 'Offline copy: check-offs stay in this browser.'; }
    };
  }

  // Label the refresh button as soon as the board has drawn it, and after any
  // re-render that recreates it.
  function labelRefresh() {
    var btn = document.getElementById('refresh-btn');
    if (btn && btn.dataset.cdOffline !== '1') {
      btn.dataset.cdOffline = '1';
      btn.textContent = '↻ Offline copy';
      btn.title = 'Disabled in the offline copy — no Canvas, no worker, no network.';
    }
  }

  // The board warns when its data is stale and when the Canvas token is near
  // expiry. Both describe a sync pipeline that, by definition, does not run for
  // a frozen snapshot — and they get louder every year the file sits in Drive
  // ("This board stopped updating 1,400 days ago", linking to a GitHub Actions
  // page that may no longer exist). They are removed here, and the banner says
  // they were, so the generation date at the top is the only freshness claim
  // this file makes. Coverage warnings ("N course content items not represented
  // on this board") are NOT removed: those describe the archive itself and stay
  // true forever.
  var ALARM_RE = /canvas token (expires|expired)|stopped updating|data is more than/i;
  var scrubbing = false;
  function scrubLivePipelineAlarms() {
    if (scrubbing) return;
    scrubbing = true;
    try {
      var nodes = document.querySelectorAll('#root .token-warn, #root .stale-warn');
      for (var i = 0; i < nodes.length; i++) {
        if (ALARM_RE.test(nodes[i].textContent || '')) nodes[i].remove();
      }
    } finally { scrubbing = false; }
  }

  function afterRender() { labelRefresh(); scrubLivePipelineAlarms(); }
  if (window.MutationObserver) {
    new MutationObserver(afterRender).observe(document.body, { childList: true, subtree: true });
  }
  setTimeout(afterRender, 0);
  setTimeout(function () { if (OFF.renderBlocked) OFF.renderBlocked(); }, 1500);
})();
"""


BANNER_CSS = """
:root{color-scheme:light}
#cd-offline-banner{font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
 background:#FEF3C7;border-bottom:2px solid #D97706;color:#4A3100;padding:12px 16px;margin:0}
#cd-offline-banner h1{font-size:14px;margin:0 0 6px;font-weight:700;letter-spacing:.01em}
#cd-offline-banner p{margin:4px 0}
#cd-offline-banner code{background:rgba(0,0,0,.07);padding:1px 4px;border-radius:3px;font-size:11px}
#cd-offline-banner details{margin-top:8px}
#cd-offline-banner summary{cursor:pointer;font-weight:600}
#cd-offline-banner ul{margin:6px 0 0 18px;padding:0}
/* Light only, on purpose: the board itself defines no prefers-color-scheme
   rules, so a dark-mode banner would sit dark above a light page and the
   warning box would inherit a pale colour onto a pale background. Caught by
   screenshotting the export, not by reading the CSS. Colours are stated
   explicitly rather than inherited for the same reason. */
#cd-offline-banner .cd-off-bad{background:#FEE2E2;border-left:3px solid #DC2626;
 color:#7F1D1D;padding:6px 10px;margin-top:8px}
#cd-offline-blocked{background:#FEE2E2;border-left:3px solid #DC2626;
 color:#7F1D1D;padding:6px 10px;margin-top:8px}
"""


def build_banner(stats: dict, degraded: list[str], generated: str) -> str:
    def esc(s):
        return (
            str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    sem_list = ", ".join(
        [f"{esc(stats['current_name'])} (current, {stats['current_items']} items)"]
        + [f"{esc(n)} ({c} items)" for n, _, c in stats["archived"]]
    )

    degraded_block = ""
    if degraded:
        lis = "".join(f"<li>{esc(d)}</li>" for d in degraded)
        degraded_block = (
            '<div class="cd-off-bad"><strong>Not everything could be inlined '
            f"({len(degraded)})</strong> — listed here rather than dropped "
            f"silently:<ul>{lis}</ul></div>"
        )
    else:
        degraded_block = (
            "<p>Everything the board loads at runtime was inlined successfully "
            "— no gaps.</p>"
        )

    return f"""<div id="cd-offline-banner">
  <h1>📦 Offline copy of the Canvas dashboard — generated {esc(generated)}</h1>
  <p><strong>Self-contained.</strong> This single file holds the board plus all
     its data: {stats['semesters']} semesters, {stats['items']} items,
     {stats['video_notes']} video-note links and {stats['reading_notes']}
     reading-note links. It needs no server, no GitHub, and no network. Opening
     it with <code>file://</code> is the intended way to use it.</p>
  <p><strong>Semesters inlined:</strong> {sem_list}.</p>
  <p><strong>Check-offs are NOT synced in this copy.</strong> The live board
     keeps ticks in Cloudflare KV through a worker so they follow you across
     devices. That worker is not reachable from a local file, and will not exist
     after the project winds down. Ticks you make here save to this one
     browser's local storage only — they will not appear on your phone, and a
     tick made on your phone will not appear here. Ticks already recorded when
     this copy was generated are shown as history.</p>
  <p><strong>Note links still work.</strong> 📝 Study Notes, 🗒 Transcript and
     📖 Reading Notes point at your personal Google Drive, which survives
     graduation. They open normally when you are online and signed in as
     yourself. Course links point at Canvas and stop working when Naropa
     withdraws access.</p>
  <p><strong>Archived semesters are title-and-link only</strong>, exactly as on
     the live board — no reading or lecture body text is stored in this file.</p>
  {degraded_block}
  <div id="cd-offline-blocked" hidden></div>
  <details>
    <summary>What does not work offline, and why</summary>
    <ul>
      <li><strong>Cross-device check-off sync</strong> — needs the Cloudflare
          worker at <code>/state</code>. Local storage only here.</li>
      <li><strong>↻ Refresh now</strong> — needs the worker's
          <code>/dispatch</code> and a GitHub Actions run. Disabled.</li>
      <li><strong>New Canvas data</strong> — this file is a snapshot. Nothing in
          it updates, by design.</li>
      <li><strong>Canvas course links</strong> — resolve only while Naropa
          access lasts. Drive note links are the durable ones.</li>
      <li><strong>Stale-data and token-expiry warnings are hidden</strong> —
          they describe a sync that no longer runs, and would grow more wrong
          every year. The generated date at the top of this banner is the only
          freshness claim this file makes. Warnings about archive coverage are
          kept, because those stay true.</li>
    </ul>
  </details>
</div>
"""


# ───────────────────────────────────────────────────────────────── assembly ──

def prepare_board() -> tuple[str, list[str]]:
    """Read the untouched board and remove anything that would load off-box."""
    return strip_remote_subresources(BOARD.read_text(encoding="utf-8"))


def build(html: str, bundle: dict, degraded: list[str], stats: dict,
          generated: str) -> str:

    # Anchor on structure, not line numbers — index.html is edited by other
    # sessions. A missing anchor is a hard failure: a half-injected export that
    # looks fine in source and dies in the browser is the exact outcome to avoid.
    for anchor in ("<body>", "</style>", "</body>"):
        if html.count(anchor) != 1:
            raise SystemExit(
                f"FATAL: expected exactly one {anchor!r} in {BOARD}, "
                f"found {html.count(anchor)}. The board's structure changed; "
                f"fix this exporter rather than shipping a broken file."
            )
    # Inject before the board's FIRST script so the resolver is in place before
    # any of its code runs.
    script_at = html.index("<script>")
    if script_at < html.index("<body>"):
        raise SystemExit("FATAL: unexpected <script> before <body> in the board.")

    prelude = (
        '<script id="cd-offline-bundle" type="application/json">'
        + js_json(bundle)
        + "</script>\n<script>"
        + OFFLINE_RUNTIME
        + "</script>\n"
    )

    out = html[:script_at] + prelude + html[script_at:]
    out = out.replace("</style>", BANNER_CSS + "</style>", 1)
    out = out.replace("<body>", "<body>\n" + build_banner(stats, degraded, generated), 1)
    out = out.replace("</body>", "<script>" + OFFLINE_EPILOGUE + "</script>\n</body>", 1)

    # Last gate before writing. If anything in the finished file would still
    # load from off-box, the file is not self-contained and must not ship
    # claiming to be.
    leftovers = find_remote_subresources(out)
    if leftovers:
        print("FATAL: the export still references off-box resources, so it is "
              "not self-contained:")
        for h in leftovers[:10]:
            print("   •", h)
        raise SystemExit(1)

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("-o", "--out", default=str(DEFAULT_OUT), help="output HTML path")
    ap.add_argument("--self-test", action="store_true",
                    help="run the guards against known-bad input first, to prove "
                         "they can fail, then export")
    args = ap.parse_args()

    bundle, degraded, stats = collect()

    if args.self_test and not run_self_test(bundle):
        return 1

    n_arch = archived_payload_count(bundle)
    offenders = assert_title_and_link_only(bundle)
    if offenders:
        print(f"FATAL: {len(offenders)} archived item(s) carry body text — refusing "
              f"to write an export that reintroduces stripped content:")
        for o in offenders[:10]:
            print("   •", o)
        return 1
    print(f"✓ title-and-link check: 0 archived items carry a summary "
          f"(checked {n_arch} archived payloads, "
          f"{stats['archived_items']} items)")

    board_html, font_notes = prepare_board()
    degraded += font_notes

    generated = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out_path = Path(args.out).expanduser().resolve()
    html = build(board_html, bundle, degraded, stats, generated)
    out_path.write_text(html, encoding="utf-8")

    size = out_path.stat().st_size
    digest = hashlib.sha256(html.encode("utf-8")).hexdigest()

    print(f"\n✓ wrote {out_path}")
    print(f"  size      : {size:,} bytes ({size / 1048576:.2f} MiB)")
    print(f"  sha256    : {digest}")
    print(f"  semesters : {stats['semesters']} "
          f"(1 current + {len(stats['archived'])} archived)")
    print(f"  items     : {stats['items']} "
          f"({stats['current_items']} current + {stats['archived_items']} archived)")
    print(f"  payloads  : {len(bundle)} inlined JSON documents")
    print(f"  notes     : {stats['video_notes']} video, {stats['reading_notes']} reading")
    if degraded:
        print(f"  DEGRADED  : {len(degraded)} item(s), declared in the page:")
        for d in degraded:
            print("     •", d)
    else:
        print("  degraded  : none")
    return 0


def run_self_test(real_bundle: dict) -> bool:
    """Controls. A check that cannot fail is not a check, so run each guard
    against input that MUST trip it before trusting the pass on real data."""
    print("── self-test (controls) ─────────────────────────────────────────")
    ok = True

    # Control 1: an archived payload carrying body text must be caught.
    poisoned = {
        "archive/poison.json": {
            "archived": True,
            "items": [
                {"title": "Clean row", "summary": ""},
                {"title": "Poisoned row", "summary": "Body text lifted from a Canvas page."},
            ],
        }
    }
    hits = assert_title_and_link_only(poisoned)
    print(f"  [{'PASS' if len(hits) == 1 else 'FAIL'}] poisoned archive → "
          f"{len(hits)} offender(s), expected 1")
    ok &= len(hits) == 1

    # Control 2: the SAME body text under a non-archived payload must NOT be
    # caught — otherwise the guard is just "any summary anywhere" and would
    # flag the live board, where summaries are intended.
    live = {"data.json": {"items": [{"title": "x", "summary": "intended live text"}]}}
    hits2 = assert_title_and_link_only(live)
    print(f"  [{'PASS' if not hits2 else 'FAIL'}] live (non-archived) payload → "
          f"{len(hits2)} offender(s), expected 0")
    ok &= not hits2

    # Control 3: the guard must actually be looking at the real archives. If it
    # inspected zero payloads, its clean result on real data means nothing.
    n = archived_payload_count(real_bundle)
    print(f"  [{'PASS' if n >= 1 else 'FAIL'}] real bundle exposes {n} archived "
          f"payload(s) to the guard, expected >= 1")
    ok &= n >= 1

    # Control 4: real current-semester data must contain summaries. If it did
    # not, the whole invariant would be vacuous — nothing to strip.
    cur = real_bundle.get("data.json") or {}
    live_sums = sum(1 for i in (cur.get("items") or []) if (i.get("summary") or "").strip())
    print(f"  [{'PASS' if live_sums > 0 else 'FAIL'}] live board carries "
          f"{live_sums} non-empty summaries (proves archives were stripped, "
          f"not merely empty), expected > 0")
    ok &= live_sums > 0

    # Control 5: the subresource scanner must catch a remote load. This is the
    # check that was missing when the first export shipped with four live
    # requests to Google Fonts, so it gets a control of its own.
    bad = '<head><link rel="stylesheet" href="https://fonts.googleapis.com/css2?x"></head>'
    print(f"  [{'PASS' if find_remote_subresources(bad) else 'FAIL'}] scanner "
          f"catches a remote <link>, expected >= 1 hit")
    ok &= bool(find_remote_subresources(bad))

    stripped, notes = strip_remote_subresources(bad)
    clean = not find_remote_subresources(stripped) and len(notes) == 1
    print(f"  [{'PASS' if clean else 'FAIL'}] stripper removes it and declares "
          f"it ({len(notes)} note)")
    ok &= clean

    # Control 6: a clickable <a href> to Drive must NOT be mistaken for a
    # subresource — otherwise the gate would reject every real export.
    link_ok = not find_remote_subresources(
        '<a class="note-link" href="https://docs.google.com/document/d/abc">x</a>')
    print(f"  [{'PASS' if link_ok else 'FAIL'}] scanner ignores a clickable "
          f"Drive link, expected 0 hits")
    ok &= link_ok

    # Control 7: the board actually HAS remote font links to strip. If upstream
    # ever inlines them, this control fails loudly instead of the stripper
    # quietly becoming a no-op nobody notices.
    board_hits = find_remote_subresources(BOARD.read_text(encoding="utf-8"))
    print(f"  [{'PASS' if board_hits else 'INFO'}] upstream board has "
          f"{len(board_hits)} remote subresource(s) for the stripper to remove")

    # Control 8: the JS escaper must neutralise a script-closing payload.
    nasty = js_json({"t": "</script><script>alert(1)</script>"})
    print(f"  [{'PASS' if '</script' not in nasty.lower() else 'FAIL'}] "
          f"js_json escapes a script-breaking string")
    ok &= "</script" not in nasty.lower()

    print(f"── self-test {'passed' if ok else 'FAILED'} "
          f"───────────────────────────────────\n")
    return bool(ok)


if __name__ == "__main__":
    sys.exit(main())
