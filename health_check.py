#!/usr/bin/env python3
"""
health_check.py — is the published board actually healthy?

WHY THIS IS A SCRIPT AND NOT A WORKFLOW HEREDOC
    These checks used to live inline in .github/workflows/health.yml. Workflow
    files need the `workflow` PAT scope to push, which this project's token does
    not have, so every change to the checks had to be pasted into GitHub's web
    editor by hand. That friction is why the checks went un-updated. As a repo
    script it can be pushed like any other file, and health.yml shrinks to one
    line that calls it.

WHAT IT IS FOR
    The dangerous failure here is SILENT: when the Canvas token dies, the site
    keeps serving the last good data.json and looks completely normal. That went
    unnoticed for three weeks in July 2026. A check inside the sync cannot report
    that the sync stopped running, so this runs on its own schedule and fails
    loudly — GitHub emails the owner on a failed run, and that email is the alert.

FROZEN BOARDS
    After graduation the board is a finished archive: it stops updating on
    purpose. Staleness, token runway and coverage are then meaningless, and
    failing on them would email a weekly complaint about a board that is working
    exactly as intended. When the board reports `frozen: true` this runs
    integrity checks only.

THE COUNTDOWN
    Every other check here reports damage after it happens. The access_ends
    countdown is the only one that fires BEFORE: once Canvas access ends nothing
    can be re-fetched, so a warning that arrives afterwards is worthless.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.request

BOARD_URL = os.environ.get(
    "BOARD_URL", "https://jtbdashboard.fitzsimmons.org/dashboard/data.json")
REPO = os.environ.get("GITHUB_REPOSITORY", "tbfitzsimmons/canvas-dashboard")
ACCESS_ENDS_DEFAULT = "2027-05-15"

# Days before access_ends at which the countdown stops warning and starts
# failing. 30 days is enough to run a final capture and fix what it finds.
COUNTDOWN_FAIL_DAYS = 30
COUNTDOWN_WARN_DAYS = 90


def fetch_json(url: str, token: str | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": "dashboard-health-check"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def err(msg: str) -> None:
    """GitHub Actions surfaces ::error:: lines in the run summary and the email."""
    print(f"::error::{msg}")


def check_integrity(d: dict, fail: list) -> None:
    """Cheap structural checks that are meaningful whether or not it is frozen."""
    items = d.get("items")
    courses = d.get("courses")
    if not isinstance(items, list) or not items:
        fail.append("board has no items — data.json is empty or malformed")
    if not isinstance(courses, list) or not courses:
        fail.append("board has no courses — data.json is empty or malformed")
    archives = d.get("archives") or []
    print(f"items          : {len(items) if isinstance(items, list) else '?'}")
    print(f"courses        : {len(courses) if isinstance(courses, list) else '?'}")
    print(f"archived boards: {len(archives)}")
    if not archives:
        fail.append("no archived semesters listed — the archive index is missing")


def check_countdown(d: dict, fail: list) -> None:
    """Warn, then fail, as Canvas access approaches. Fires BEFORE the damage."""
    raw = (d.get("access_ends") or ACCESS_ENDS_DEFAULT).strip()
    try:
        ends = dt.date.fromisoformat(raw)
    except ValueError:
        print(f"access_ends    : {raw!r} unparseable, using {ACCESS_ENDS_DEFAULT}")
        ends = dt.date.fromisoformat(ACCESS_ENDS_DEFAULT)
    left = (ends - dt.date.today()).days
    print(f"access ends    : {ends}  ({left} days)")

    if left < 0 or left > COUNTDOWN_WARN_DAYS:
        return
    msg = (f"Canvas access ends in {left} days ({ends}). Nothing can be re-fetched "
           f"afterwards. Final capture checklist: (1) archive the running semester, "
           f"(2) re-run build_archive_urls.py and repoint_external_links.py, "
           f"(3) confirm the archive has swept every term, (4) retrieve or write off "
           f"the Kaltura-embedded media, (5) sync.py --freeze.")
    if left <= COUNTDOWN_FAIL_DAYS:
        fail.append(msg)
    else:
        print(f"::warning::{msg}")


def check_live(d: dict, fail: list) -> None:
    """Checks that only make sense while the board is still being synced."""
    gen = dt.datetime.fromisoformat(d["generated_at"])
    age = (dt.datetime.now(dt.timezone.utc) - gen).days
    print(f"last sync      : {d['generated_at'][:16]}  ({age} days ago)")
    if age > 9:
        fail.append(f"board is {age} days stale — the weekly sync has missed a run")

    # Prefer the value sync.py MEASURED from Canvas over config.json's hand-typed
    # token_expires, which extend_tokens() silently outruns. On 2026-11-19 the
    # string said 2026-12-01 and Canvas said 2026-12-11; checking the string was
    # one day from emailing a false alarm.
    exp = d.get("token_expires_live") or d.get("token_expires")
    src = "measured" if d.get("token_expires_live") else "config (unmeasured)"
    left = (dt.date.fromisoformat(exp) - dt.date.today()).days if exp else None
    print(f"token expires  : {exp}  ({left} days)  [{src}]")
    # extend_tokens() should keep this near 29. A low number means in-place
    # renewal has stopped, which is the early warning before an outage.
    #
    # NOTE: config.json's token_expires is only as good as what was typed in, and
    # extend_tokens() moves the real expiry silently. A comfortable number here
    # is not proof of a healthy token; it is proof of a comfortable string.
    if left is not None and left < 12:
        fail.append(f"token has only {left} days left — extend_tokens() has stopped renewing it")

    c = d.get("coverage") or {}
    for name in ("assignments", "discussions"):
        k = c.get(name) or {}
        cap, tot = k.get("captured"), k.get("total")
        print(f"{name:<15}: {cap}/{tot}")
        if cap is not None and tot is not None and cap < tot:
            fail.append(f"{tot - cap} graded {name} missing from the board")

    noise = (c.get("quality") or {}).get("suspect_pct")
    print(f"noise          : {noise}%")
    if noise is not None and noise > 5:
        fail.append(f"item noise at {noise}% (normal is 1-3%)")


def check_sync_runs(fail: list) -> None:
    """A board can look fine while the workflow behind it has been failing."""
    url = f"https://api.github.com/repos/{REPO}/actions/workflows/sync.yml/runs?per_page=5"
    try:
        runs = fetch_json(url, os.environ.get("GITHUB_TOKEN")).get("workflow_runs", [])
    except Exception as exc:
        print(f"::warning::could not read sync runs ({type(exc).__name__}: {exc})")
        return
    for r in runs:
        print(f"  {r['created_at'][:16]}  {r.get('conclusion') or r.get('status')}")
    bad = [r for r in runs if r.get("conclusion") not in ("success", None)]
    if bad:
        fail.append(f"{len(bad)} of the last {len(runs)} sync runs did not succeed")


def main() -> int:
    try:
        d = fetch_json(BOARD_URL)
    except Exception as exc:
        err(f"could not fetch {BOARD_URL}: {type(exc).__name__}: {exc}")
        return 1

    fail: list[str] = []
    frozen = bool(d.get("frozen"))

    if frozen:
        print("=" * 68)
        print("  BOARD IS FROZEN — a completed archive, not a live dashboard.")
        print("=" * 68)
        print(f"frozen at      : {d.get('frozen_at', '?')}")
        print(f"reason         : {d.get('frozen_reason', '?')}")
        print("Staleness, token runway and coverage are not checked: the board")
        print("stops updating on purpose, and failing on that would email a")
        print("weekly complaint about a board that is working correctly.\n")
        check_integrity(d, fail)
    else:
        check_integrity(d, fail)
        print()
        check_live(d, fail)
        print()
        check_countdown(d, fail)
        print()
        check_sync_runs(fail)

    if fail:
        print("\n::error::DASHBOARD UNHEALTHY")
        for f in fail:
            err(f)
        print(f"\nSync log: https://github.com/{REPO}/actions/workflows/sync.yml")
        print("sync.py prints Canvas's real expired_at from the 401 body when a token dies.")
        print("Canvas caps student tokens at 30 days; a replacement must be made by hand")
        print("(Account -> Settings -> New Access Token) and pasted into the CANVAS_TOKEN secret.")
        return 1

    print("\nAll checks passed." + ("  (frozen board: integrity only)" if frozen else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
