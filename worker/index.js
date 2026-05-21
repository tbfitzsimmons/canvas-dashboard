/**
 * canvas-dispatch — Cloudflare Worker (dashboard-sync)
 *
 * Two endpoints:
 *   POST /dispatch  — triggers GitHub Actions workflow_dispatch via GH_TOKEN secret
 *   GET  /state     — reads check-off state from KV (auth: SHARED_TOKEN)
 *   PUT  /state     — writes check-off state to KV  (auth: SHARED_TOKEN)
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
      const methods = pathname === '/dispatch' ? 'POST, OPTIONS' : 'GET, PUT, OPTIONS';
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

    return new Response(JSON.stringify({ ok: false, error: 'Not found' }),
      { status: 404, headers: { ...cors(origin), 'Content-Type': 'application/json' } });
  },
};
