#!/usr/bin/env python3
"""
repoint_external_links.py — point archived rows straight at their off-Canvas URL.

WHY THIS EXISTS
    `build_archive_urls.py` deliberately refuses `external:` targets with
    `external_url_not_in_drive`, and it is right to: an off-Canvas URL has no
    Google Drive copy, so writing it into `archive_url` would render a YouTube
    video under a "Drive copy" label, which is a lie.

    But those rows still die at graduation. Their `link` is a Canvas
    `module_item_redirect` — a wrapper whose ONLY function is to bounce the
    browser to an external address. Canvas withdraws access ~May 2027 and the
    wrapper dies, taking a perfectly good YouTube or counseling.org link with it.
    The destination was already captured at sync time in `canvas_target`.

    So this script does the one safe thing: replaces the dying wrapper with the
    destination it wrapped. Nothing is lost, because a redirect carries no
    content of its own.

WHAT IT TOUCHES
    `dashboard/archive/*.json` only — never `data.json`. The CURRENT board is
    regenerated from Canvas on every sync, so an edit there would be overwritten
    within a day; its external rows are handled when it is archived at rollover.

    It sets `link` to the external URL and preserves the original Canvas wrapper
    in `canvas_link`, so the change is reversible and the run is idempotent.

WHAT IT REFUSES
    - non-https targets (3 rows are bare `http://`; upgrading them would be a
      guess about someone else's TLS, not a fact)
    - hosts tied to her Naropa or Zoom account, which die WITH Canvas access and
      are no more durable than the wrapper they would replace
    - `page_child` rows, where `canvas_target` names the page the row was
      extracted FROM rather than where it points
    - rows with no `external:` target

    Every refusal is proven able to fire by `--control` before any board is read.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import sys
from urllib.parse import urlparse

# Hosts that stop answering for her when Naropa de-provisions the account. A
# link to one of these is NOT durable, so replacing a Canvas wrapper with one
# buys nothing.
# Matched as a SUBSTRING of the host, so each entry must be chosen to survive
# subdomain variation. An earlier version listed "naropa.sharepoint.com", which
# silently failed to match "naropa-my.sharepoint.com" — the hyphen defeated it —
# and one row was re-pointed at a SharePoint URL that dies with her account.
# Hence "naropa" bare (catches naropa.edu, my.naropa.edu, naropa-my.*,
# naropa.zoom.us) and "sharepoint.com" bare (any SharePoint tenant is
# institution-hosted, not hers).
ACCOUNT_TIED = (
    "zoom.us", "naropa", "sharepoint.com", "simplesyllabus.com",
    "panopto", "supervisionassist.com",
)
CANVAS_HOST = re.compile(r"\binstructure\.com\b", re.I)
EXTERNAL_PREFIX = "external:"


def classify(it: dict):
    """(url_or_None, reason). reason names the winning rule or the refusal."""
    link = (it.get("link") or "").strip()
    target = (it.get("canvas_target") or "").strip()

    if not target.startswith(EXTERNAL_PREFIX):
        return None, "no_external_target"
    url = target[len(EXTERNAL_PREFIX):].strip()

    # canvas_target means something different for page_child rows: it names the
    # containing page, not the destination. Never rewrite a link from it.
    if it.get("source") == "page_child":
        return None, "page_child_target_is_container"

    if not url.startswith("https://"):
        return None, "target_not_https"

    host = (urlparse(url).netloc or "").lower().replace("www.", "")
    if not host:
        return None, "target_has_no_host"
    if any(t in host for t in ACCOUNT_TIED):
        return None, "target_account_tied"

    # Already re-pointed by an earlier run.
    if link == url:
        return url, "already_repointed"

    # Only ever replace a Canvas link. A row already pointing somewhere else is
    # not ours to touch.
    if not link or not CANVAS_HOST.search(link):
        return None, "link_not_canvas"

    return url, "repoint"


def run_control() -> bool:
    """Prove the classifier can both accept and refuse before trusting it."""
    REAL = "https://www.youtube.com/watch?v=AyQdeYjXUhE"
    WRAP = "https://naropa.instructure.com/api/v1/courses/7208/module_item_redirect/306896"
    cases = [
        ("real external on a wrapper", {"link": WRAP, "canvas_target": f"external:{REAL}",
                                        "source": "module_item"}, "repoint"),
        ("idempotent re-run", {"link": REAL, "canvas_target": f"external:{REAL}",
                               "source": "module_item"}, "already_repointed"),
        ("http target", {"link": WRAP, "canvas_target": "external:http://vimeo.com/451530204",
                         "source": "module_item"}, "target_not_https"),
        ("account-tied target", {"link": WRAP, "canvas_target": "external:https://naropa.zoom.us/rec/x",
                                 "source": "module_item"}, "target_account_tied"),
        # Regression cases: these hosts defeated the first version of the matcher.
        ("naropa-my sharepoint", {"link": WRAP,
                                  "canvas_target": "external:https://naropa-my.sharepoint.com/x",
                                  "source": "module_item"}, "target_account_tied"),
        ("any sharepoint tenant", {"link": WRAP,
                                   "canvas_target": "external:https://other.sharepoint.com/x",
                                   "source": "module_item"}, "target_account_tied"),
        ("my.naropa.edu", {"link": WRAP, "canvas_target": "external:https://my.naropa.edu/portal",
                           "source": "module_item"}, "target_account_tied"),
        ("page_child container", {"link": WRAP, "canvas_target": f"external:{REAL}",
                                  "source": "page_child"}, "page_child_target_is_container"),
        ("no external target", {"link": WRAP, "canvas_target": "page:7208:week-1",
                                "source": "module_item"}, "no_external_target"),
        ("empty target", {"link": WRAP, "canvas_target": "", "source": "module_item"},
         "no_external_target"),
        ("link already off-Canvas", {"link": "https://example.org/a",
                                     "canvas_target": f"external:{REAL}",
                                     "source": "module_item"}, "link_not_canvas"),
        ("hostless target", {"link": WRAP, "canvas_target": "external:https:///nohost",
                             "source": "module_item"}, "target_has_no_host"),
    ]
    ok = True
    for name, item, expect in cases:
        _, got = classify(item)
        good = got == expect
        ok &= good
        print(f"  [{'ok ' if good else 'FAIL'}] {name:30} expect={expect:32} got={got}")
    print("  control passed: the rule can accept AND refuse."
          if ok else "  CONTROL FAILED — refusing to touch any board.")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board-dir", default="dashboard/archive")
    ap.add_argument("--apply", action="store_true",
                    help="write the boards (default is a dry run)")
    ap.add_argument("--control", action="store_true", help="run the control harness and exit")
    a = ap.parse_args()

    print("CONTROL")
    if not run_control():
        return 2
    if a.control:
        return 0

    boards = sorted(p for p in glob.glob(os.path.join(a.board_dir, "*.json"))
                    if os.path.basename(p) != "index.json")
    if not boards:
        sys.exit(f"FATAL: no board files in {a.board_dir}")

    reasons = collections.Counter()
    hosts = collections.Counter()
    per_board = {}
    regressions = []
    healed = []
    writes = {}

    for p in boards:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        items = d.get("items") or []
        changed = already = 0
        for it in items:
            url, reason = classify(it)
            reasons[reason] += 1

            # Self-heal: a row an earlier run re-pointed, which this run would
            # now REFUSE, was re-pointed in error. Put the original wrapper
            # back rather than leaving a bad link in place. This is what makes
            # the script safe to tighten: fixing a rule also repairs its damage.
            if it.get("canvas_link") and reason not in ("repoint", "already_repointed"):
                healed.append((os.path.basename(p), it.get("title", "?")[:46], reason))
                if a.apply:
                    it["link"] = it.pop("canvas_link")
                continue

            # Regression guard: a row re-pointed earlier whose target vanished
            # from canvas_target entirely — distinct from the above, and not
            # self-healable, because there is nothing to fall back to.
            if it.get("canvas_link") and not (it.get("canvas_target") or ""):
                regressions.append((os.path.basename(p), it.get("title", "?")[:46], reason))

            if reason == "already_repointed":
                already += 1
            elif reason == "repoint":
                hosts[(urlparse(url).netloc or "").lower().replace("www.", "")] += 1
                changed += 1
                if a.apply:
                    it.setdefault("canvas_link", it["link"])
                    it["link"] = url
        per_board[os.path.basename(p)[:-5]] = (len(items), changed, already)
        if a.apply and (changed or any(h[0] == os.path.basename(p) for h in healed)):
            writes[p] = d

    print("\nPER SEMESTER")
    print(f"  {'semester':16} {'items':>6} {'to repoint':>11} {'already':>8}")
    tot_c = tot_a = tot_i = 0
    for name, (n, c, al) in per_board.items():
        print(f"  {name:16} {n:>6} {c:>11} {al:>8}")
        tot_i += n; tot_c += c; tot_a += al
    print(f"  {'TOTAL':16} {tot_i:>6} {tot_c:>11} {tot_a:>8}")

    print("\nDESTINATION HOSTS")
    for h, c in hosts.most_common(12):
        print(f"  {c:4}  {h}")

    print("\nOUTCOMES")
    for r, c in reasons.most_common():
        print(f"  {c:5}  {r}")

    if healed:
        print(f"\nSELF-HEALED — {len(healed)} row(s) re-pointed in error by an earlier "
              f"run, original Canvas link restored:")
        for b, t, r in healed[:10]:
            print(f"     [{b}] {t} -> {r}")

    if regressions:
        print(f"\nREGRESSION GUARD — {len(regressions)} row(s) re-pointed by an earlier run "
              f"no longer resolve:")
        for b, t, r in regressions[:10]:
            print(f"     [{b}] {t} -> {r}")
        print("  REFUSING to write. A target disappeared; investigate before re-running.")
        return 3

    if not a.apply:
        print(f"\nDRY RUN — nothing written. {tot_c} row(s) would be re-pointed.")
        return 0

    for p, d in writes.items():
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, p)
    print(f"\nWROTE {len(writes)} board file(s); {tot_c} row(s) re-pointed, "
          f"{len(healed)} healed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
