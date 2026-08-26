/**
 * canvas-dispatch — Cloudflare Worker (dashboard-sync)
 *
 * Endpoints:
 *   POST /dispatch        — triggers GitHub Actions workflow_dispatch via GH_TOKEN
 *   GET  /state           — reads check-off state from KV   (auth: SHARED_TOKEN)
 *   PUT  /state           — writes check-off state to KV    (auth: SHARED_TOKEN)
 *   GET  /backups         — lists nightly snapshot dates    (auth: SHARED_TOKEN)
 *   GET  /backups/<date>  — fetches one snapshot            (auth: SHARED_TOKEN)
 *   POST /backups/run     — snapshot now, on demand         (auth: SHARED_TOKEN)
 *
 * Cron: a daily trigger (Settings → Triggers) calls scheduled(), which copies
 * `checkoffs` to `backup:<YYYY-MM-DD>` and keeps the newest KEEP_BACKUPS.
 * A semester of progress otherwise lives in exactly one KV key.
 *
 * RESTORE (no portal needed):
 *   curl -H "Authorization: Bearer $T" .../backups                → pick a date
 *   curl -H "Authorization: Bearer $T" .../backups/2026-08-27 > s.json
 *   curl -X PUT -H "Authorization: Bearer $T" -H 'Content-Type: application/json' \
 *        --data-binary @s.json .../state
 *
 * Deploy: paste this file into the Cloudflare portal worker editor and click Deploy.
 * Then set these secrets under Settings → Variables and Secrets:
 *   GH_TOKEN     — GitHub PAT with workflow:write scope
 *   SHARED_TOKEN — random string shared with the dashboard URL (#t=<token>)
 * And bind the KV namespace STATE under Settings → Variables and Secrets → KV.
 *
 * Allowed origins:
 *   https://jtbdashboard.fitzsimmons.org  (primary custom domain)
 *   https://tbfitzsimmons.github.io       (legacy GitHub Pages URL)
 */

const ALLOWED_ORIGINS = new Set([
  'https://jtbdashboard.fitzsimmons.org',
  'https://tbfitzsimmons.github.io',
]);

const STATE_KEY = 'checkoffs';
const BACKUP_PREFIX = 'backup:';
const KEEP_BACKUPS = 14;   // ~14 × ~50KB — trivial against the 1GB free tier

/** Copy the live check-off blob to a dated snapshot, then prune old ones. */
async function snapshotState(env) {
  const val = await env.STATE.get(STATE_KEY);
  if (!val) return { ok: false, skipped: 'no state to snapshot' };

  const date = new Date().toISOString().slice(0, 10);   // YYYY-MM-DD
  await env.STATE.put(BACKUP_PREFIX + date, val);

  // Keep the newest KEEP_BACKUPS. ISO dates sort lexicographically.
  const listed = await env.STATE.list({ prefix: BACKUP_PREFIX });
  const names = listed.keys.map(k => k.name).sort().reverse();
  const stale = names.slice(KEEP_BACKUPS);
  for (const name of stale) await env.STATE.delete(name);

  return { ok: true, date, total: Math.min(names.length, KEEP_BACKUPS), pruned: stale.length };
}

function cors(origin, methods = 'GET, PUT, OPTIONS') {
  const allow = ALLOWED_ORIGINS.has(origin) ? origin : '';
  return {
    'Access-Control-Allow-Origin': allow,
    'Access-Control-Allow-Methods': methods,
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    'Access-Control-Max-Age': '86400',
  };
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get('Origin') || '';
    const { pathname } = new URL(request.url);

    // ── CORS preflight ──────────────────────────────────────────────
    if (request.method === 'OPTIONS') {
      const methods = pathname === '/dispatch' ? 'POST, OPTIONS'
        : pathname.startsWith('/backups') ? 'GET, POST, OPTIONS'
        : 'GET, PUT, OPTIONS';
      return new Response(null, { status: 204, headers: cors(origin, methods) });
    }

    // ── /dispatch  — trigger GitHub Actions workflow ─────────────────
    if (pathname === '/dispatch') {
      if (request.method !== 'POST') {
        return new Response(JSON.stringify({ ok: false, error: 'Method not allowed' }),
          { status: 405, headers: { ...cors(origin, 'POST, OPTIONS'), 'Content-Type': 'application/json' } });
      }
      if (!env.GH_TOKEN) {
        return new Response(JSON.stringify({ ok: false, error: 'GH_TOKEN secret not configured' }),
          { status: 500, headers: { ...cors(origin, 'POST, OPTIONS'), 'Content-Type': 'application/json' } });
      }
      try {
        const ghRes = await fetch(
          'https://api.github.com/repos/tbfitzsimmons/canvas-dashboard/actions/workflows/sync.yml/dispatches',
          {
            method: 'POST',
            headers: {
              'Authorization': `Bearer ${env.GH_TOKEN}`,
              'Accept': 'application/vnd.github+json',
              'Content-Type': 'application/json',
              'User-Agent': 'canvas-dispatch-worker/1.0',
              'X-GitHub-Api-Version': '2022-11-28',
            },
            body: JSON.stringify({ ref: 'main' }),
          }
        );
        if (!ghRes.ok) {
          const body = await ghRes.text().catch(() => '');
          return new Response(
            JSON.stringify({ ok: false, error: `GitHub ${ghRes.status}: ${body}` }),
            { status: 502, headers: { ...cors(origin, 'POST, OPTIONS'), 'Content-Type': 'application/json' } }
          );
        }
        return new Response(JSON.stringify({ ok: true }),
          { status: 200, headers: { ...cors(origin, 'POST, OPTIONS'), 'Content-Type': 'application/json' } });
      } catch (e) {
        return new Response(JSON.stringify({ ok: false, error: e.message }),
          { status: 500, headers: { ...cors(origin, 'POST, OPTIONS'), 'Content-Type': 'application/json' } });
      }
    }

    // ── /state  — cross-device check-off sync (KV) ───────────────────
    if (pathname === '/state') {
      const token = (request.headers.get('Authorization') || '').replace('Bearer ', '');
      if (!env.SHARED_TOKEN || token !== env.SHARED_TOKEN) {
        return new Response(JSON.stringify({ ok: false, error: 'Unauthorized' }),
          { status: 401, headers: { ...cors(origin), 'Content-Type': 'application/json' } });
      }

      if (request.method === 'GET') {
        const val = await env.STATE.get('checkoffs');
        return new Response(val || '{}',
          { status: 200, headers: { ...cors(origin), 'Content-Type': 'application/json' } });
      }

      if (request.method === 'PUT') {
        const body = await request.text();
        await env.STATE.put('checkoffs', body);
        return new Response(JSON.stringify({ ok: true }),
          { status: 200, headers: { ...cors(origin), 'Content-Type': 'application/json' } });
      }

      return new Response(JSON.stringify({ ok: false, error: 'Method not allowed' }),
        { status: 405, headers: { ...cors(origin), 'Content-Type': 'application/json' } });
    }

    // ── /backups — nightly snapshots of the check-off state ──────────
    if (pathname === '/backups' || pathname.startsWith('/backups/')) {
      const token = (request.headers.get('Authorization') || '').replace('Bearer ', '');
      if (!env.SHARED_TOKEN || token !== env.SHARED_TOKEN) {
        return new Response(JSON.stringify({ ok: false, error: 'Unauthorized' }),
          { status: 401, headers: { ...cors(origin, 'GET, POST, OPTIONS'), 'Content-Type': 'application/json' } });
      }
      const jsonHeaders = { ...cors(origin, 'GET, POST, OPTIONS'), 'Content-Type': 'application/json' };

      // POST /backups/run — take a snapshot immediately
      if (pathname === '/backups/run' && request.method === 'POST') {
        const result = await snapshotState(env);
        return new Response(JSON.stringify(result), { status: 200, headers: jsonHeaders });
      }

      // GET /backups — list available snapshot dates
      if (pathname === '/backups' && request.method === 'GET') {
        const listed = await env.STATE.list({ prefix: BACKUP_PREFIX });
        const dates = listed.keys.map(k => k.name.slice(BACKUP_PREFIX.length)).sort().reverse();
        return new Response(JSON.stringify({ ok: true, count: dates.length, backups: dates }),
          { status: 200, headers: jsonHeaders });
      }

      // GET /backups/<date> — fetch one snapshot verbatim
      if (request.method === 'GET') {
        const date = pathname.slice('/backups/'.length);
        if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
          return new Response(JSON.stringify({ ok: false, error: 'Expected /backups/YYYY-MM-DD' }),
            { status: 400, headers: jsonHeaders });
        }
        const val = await env.STATE.get(BACKUP_PREFIX + date);
        if (!val) {
          return new Response(JSON.stringify({ ok: false, error: 'No snapshot for ' + date }),
            { status: 404, headers: jsonHeaders });
        }
        return new Response(val, { status: 200, headers: jsonHeaders });
      }

      return new Response(JSON.stringify({ ok: false, error: 'Method not allowed' }),
        { status: 405, headers: jsonHeaders });
    }

    return new Response(JSON.stringify({ ok: false, error: 'Not found' }),
      { status: 404, headers: { ...cors(origin), 'Content-Type': 'application/json' } });
  },

  /** Cron entry point — see the Triggers tab in the Cloudflare portal. */
  async scheduled(event, env, ctx) {
    ctx.waitUntil(
      snapshotState(env).then(r => console.log('nightly snapshot:', JSON.stringify(r)))
    );
  },
};
