/**
 * MSW AI edge-only migration: proxy JUST the robot centre to Render.
 * Do not rewrite, delete, or reconfigure the rest of WordPress website.
 *
 * Cloudflare Worker environment:
 *   MSW_ROBOT_ORIGIN = "https://<name>.onrender.com"
 * Route: mobilny-serwis-warszawa.pl/msw-ai-centrum-robotow*
 * Optional: www.mobilny-serwis-warszawa.pl/msw-ai-centrum-robotow*
 */
export default {
  async fetch(request, env) {
    const incoming = new URL(request.url);
    if (!/^\/msw-ai-centrum-robotow(?:\/|$)/.test(incoming.pathname)) {
      return fetch(request);
    }
    let upstream;
    try { upstream = new URL(env.MSW_ROBOT_ORIGIN); } catch (_) {
      return new Response('MSW AI Robot Centre origin is not configured', { status: 503 });
    }
    if (upstream.protocol !== 'https:' || upstream.username || upstream.password || upstream.pathname !== '/') {
      return new Response('Invalid upstream configuration', { status: 503 });
    }
    const dest = new URL(incoming.pathname + incoming.search, upstream.origin);
    const headers = new Headers(request.headers);
    headers.delete('cookie'); // Do not leak WordPress sessions to the new application.
    headers.delete('authorization'); // Avoid forwarding WP credentials to a different origin.
    // NOTE: Agent bearer tokens MUST reach the new API; restore only for agent/event routes.
    if (/^\/msw-ai-centrum-robotow\/api\/v1\/(?:agents\/[\w-]+\/events|events\/ingest)$/.test(incoming.pathname)) {
      const bearer = request.headers.get('authorization');
      if (bearer && /^Bearer [A-Za-z0-9_-]{30,200}$/.test(bearer)) headers.set('authorization',bearer);
    }
    const response = await fetch(new Request(dest.toString(), {
      method: request.method,
      headers,
      body: ['GET', 'HEAD'].includes(request.method) ? undefined : request.body,
      redirect: 'manual'
    }));
    const result = new Response(response.body, response);
    result.headers.set('X-MSW-App', 'robot-intelligence-independent');
    result.headers.set('X-Content-Type-Options', 'nosniff');
    // Security: don't cache auth or dynamic event responses at the edge.
    if (incoming.pathname.includes('/api/')) result.headers.set('Cache-Control','no-store');
    return result;
  }
};
