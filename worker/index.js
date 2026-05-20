/**
 * canvas-dispatch — Cloudflare Worker
 *
 * Proxies workflow_dispatch to GitHub so the GitHub PAT lives here as a
 * Worker secret (GH_TOKEN) rather than in the browser URL.
 *
 * Deploy:
 *   cd worker
 *   wrangler secret put GH_TOKEN        ← paste the PAT when prompted
 *   wrangler deploy
 *
 * After deploying, remove &gh=... from your bookmark URL — the button
 * will call this worker directly, no token in the URL needed.
 */

const GITHUB_REPO  = 'tbfitzsimmons/canvas-dashboard';
const WORKFLOW_REF = 'main';
const ALLOWED_ORIGIN = 'https://tbfitzsimmons.github.io';

export default {
  async fetch(request, env) {
    const origin = request.headers.get('Origin') || '';
    const corsHeaders = {
      'Access-Control-Allow-Origin': ALLOWED_ORIGIN,
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Max-Age': '86400',
      'Content-Type': 'application/json',
    };

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: corsHeaders });
    }

    const url = new URL(request.url);
    if (url.pathname !== '/dispatch') {
      return new Response(JSON.stringify({ ok: false, error: 'Not found' }), { status: 404, headers: corsHeaders });
    }
    if (request.method !== 'POST') {
      return new Response(JSON.stringify({ ok: false, error: 'Method not allowed' }), { status: 405, headers: corsHeaders });
    }

    if (!env.GH_TOKEN) {
      return new Response(JSON.stringify({ ok: false, error: 'GH_TOKEN secret not configured' }), { status: 500, headers: corsHeaders });
    }

    try {
      const ghRes = await fetch(
        `https://api.github.com/repos/${GITHUB_REPO}/actions/workflows/sync.yml/dispatches`,
        {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${env.GH_TOKEN}`,
            'Accept': 'application/vnd.github+json',
            'Content-Type': 'application/json',
            'User-Agent': 'canvas-dispatch-worker/1.0',
            'X-GitHub-Api-Version': '2022-11-28',
          },
          body: JSON.stringify({ ref: WORKFLOW_REF }),
        }
      );

      if (!ghRes.ok) {
        const body = await ghRes.text().catch(() => '');
        return new Response(
          JSON.stringify({ ok: false, error: `GitHub ${ghRes.status}: ${body}` }),
          { status: 502, headers: corsHeaders }
        );
      }

      return new Response(JSON.stringify({ ok: true }), { status: 200, headers: corsHeaders });
    } catch (e) {
      return new Response(JSON.stringify({ ok: false, error: e.message }), { status: 500, headers: corsHeaders });
    }
  },
};
