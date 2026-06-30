// security.mjs — shared hardening for BANKON Express services (WaaS + Console).
//
// Non-breaking by default: auth is OFF unless BANKON_API_TOKEN is set, so local
// dev/UI keep working. Rate limiting is generous. No request body is ever logged.

// Optional bearer-token auth. Set BANKON_API_TOKEN to require it in production.
export function apiAuth() {
  const token = process.env.BANKON_API_TOKEN || '';
  return (req, res, next) => {
    if (!token) return next();                       // disabled in dev
    const hdr = req.get('authorization') || '';
    const got = hdr.startsWith('Bearer ') ? hdr.slice(7) : (req.get('x-api-token') || '');
    if (got && timingSafeEqual(got, token)) return next();
    return res.status(401).json({ ok: false, error: 'unauthorized — provide a valid API token' });
  };
}

// Simple in-memory sliding-window rate limiter (per client IP).
export function rateLimit({ windowMs = 60000, max = 120 } = {}) {
  const hits = new Map();   // ip -> [timestamps]
  return (req, res, next) => {
    const now = Date.now();
    const ip = req.ip || req.socket?.remoteAddress || 'local';
    const arr = (hits.get(ip) || []).filter(t => now - t < windowMs);
    arr.push(now); hits.set(ip, arr);
    if (arr.length > max) return res.status(429).json({ ok: false, error: 'rate limit exceeded — slow down' });
    next();
  };
}

// Constant-time compare so token checks don't leak length/contents via timing.
function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let r = 0; for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}
