# Make Your Own Canvas Dashboard

This repo is one student's live deployment — it works, but several values are
hardcoded to *her* infrastructure. This checklist turns a fork into **your own**
dashboard in roughly an hour. It assumes you're also a Naropa student; if not,
change `canvas_url` in `config.json` to your school's Canvas domain too.

> **Shortcut:** open your fork in [Claude Code](https://claude.com/claude-code)
> and say *"Read CLAUDE.md and FORKING.md, then walk me through making this my
> own."* The CLAUDE.md file carries the full architecture context and an AI
> assistant can drive every step below with you.

## 1. Fork & detach from the original deployment

1. Fork this repo on GitHub.
2. **Delete the `CNAME` file** — it claims the original owner's custom domain
   and will break your GitHub Pages build.
3. In `dashboard/index.html`, **delete the legacy-redirect block** near the top
   of `<script>` (the `if (location.hostname === 'tbfitzsimmons.github.io')`
   lines) — it's a migration shim for the original deployment.

## 2. Personalize the config

Edit `config.json`:
- `semester` — your term name/dates (`canvas_term_name` must match Canvas
  **letter-for-letter**; `start_date` must be a Monday).
- `excluded_course_ids` — start with `[]`; add IDs of pseudo-courses if any
  appear after your first sync.
- `instructor_overrides` — start with `{}`.
- `token_expires` — set when you create your token (step 3).

The "Update Hours in Supervision Assist" weekly to-do auto-generates only for
courses named *Practicum* or *Internship* — delete `generate_synthetic_items`'s
pattern in `sync.py` if you don't want it.

## 3. Your Canvas token

Canvas → Account → Settings → **+ New Access Token**. Copy it immediately.
Add it to your fork: Settings → Secrets and variables → Actions →
**New repository secret** named `CANVAS_TOKEN`.

⚠️ Your token = your grades and coursework. Never commit it, never paste it
into anything you don't trust.

## 4. Your Cloudflare Worker (cross-device check-off sync)

Free Cloudflare account → then in the dashboard (no CLI needed):

1. **Storage & Databases → KV** → create namespace `dashboard-state`.
2. **Workers** → create worker `dashboard-sync` → Edit code → paste
   `worker/index.js` from your fork — **first update its two hardcoded values**:
   - `ALLOWED_ORIGINS` → your Pages URL (`https://<you>.github.io`)
   - the GitHub API URL → `repos/<you>/<your-repo>/actions/workflows/sync.yml/dispatches`
3. Worker → Settings → Bindings → **KV namespace**: variable `STATE` →
   `dashboard-state`.
4. Worker → Settings → Variables and Secrets → add **Secrets**:
   - `SHARED_TOKEN` — generate one: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`
   - `GH_TOKEN` — a GitHub fine-grained PAT scoped to your repo with
     **Actions: Read and write** (powers the "Refresh Now" button).
5. Deploy.

## 5. Point the dashboard at YOUR worker

In `dashboard/index.html`, update three constants:
- `SYNC_BASE` → `https://dashboard-sync.<your-subdomain>.workers.dev/state`
- `DISPATCH_WORKER` → same host, `/dispatch`
- `GH_REPO` → `<you>/<your-repo>`

## 6. Enable Pages & first sync

1. Settings → Pages → Deploy from branch → `main`.
2. Actions tab → **Sync Canvas Data** → Run workflow.
3. Check the run log: it ends with a **coverage self-audit**
   (`✓ COVERAGE VERIFIED: N/N assignments…`). If anything is missing it names
   each item — that's your signal a classification rule needs tuning for your
   professors' conventions (see "Classification rules" in README).

## 7. Pair your devices

Open `https://<you>.github.io/<your-repo>/dashboard/#t=<YOUR_SHARED_TOKEN>`
on each device once, and bookmark **that exact URL**. The masthead should say
"Paired for cross-device sync." Check something off on one device; it appears
on the others within ~30 seconds.

## What you get

Everything described in [README.md](README.md): weekly board of every
assignment/reading/video/discussion, check-offs that sync across devices
(checks *and* unchecks), automatic done-marking for submitted work, a
continuously verified "nothing graded is missing" guarantee, Monday auto-sync,
and a Refresh Now button.
