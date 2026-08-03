/**
 * mindx_client.js — thin adapter between ARRBY and mindX (mindx.pythai.net).
 *
 * STATUS: adapter stub. mindX exposes 350+ API routes per your own notes,
 * but this environment has no way to enumerate them from here — rather than
 * invent endpoint names that would silently no-op or 404, this client is
 * written against a small, explicit interface. Give it the two real route
 * paths (a "log event" / RAGE-memory write route, and an optional
 * "advise" / signal route) and it's otherwise complete.
 *
 * Intended flow:
 *   1. Before initiateArbitrage(): optionally call mindX for a go/no-go
 *      signal (e.g. mindX's BDI layer has broader market context than a
 *      single on-chain quote does).
 *   2. After a tx confirms (or reverts): log the outcome to mindX so it
 *      publishes to rage.pythai.net and the run becomes part of RAGE memory.
 */
class MindXClient {
  /**
   * @param {object} opts
   * @param {string} opts.baseUrl        e.g. "https://mindx.pythai.net"
   * @param {string} [opts.apiKey]       if mindX's gateway requires one
   * @param {string} [opts.logPath]      e.g. "/api/v1/events"      (TODO: confirm)
   * @param {string} [opts.advisePath]   e.g. "/api/v1/arby/advise" (TODO: confirm)
   */
  constructor({ baseUrl, apiKey, logPath = '/api/v1/events', advisePath = '/api/v1/advise' }) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.apiKey = apiKey;
    this.logPath = logPath;
    this.advisePath = advisePath;
  }

  _headers() {
    const h = { 'Content-Type': 'application/json' };
    if (this.apiKey) h['Authorization'] = `Bearer ${this.apiKey}`;
    return h;
  }

  /** Ask mindX for a go/no-go before spending gas. Fails open (returns {advise:true}) if unreachable. */
  async preflightAdvise({ chainId, asset, amount, routerA, routerB, expectedProfit }) {
    try {
      const res = await fetch(`${this.baseUrl}${this.advisePath}`, {
        method: 'POST',
        headers: this._headers(),
        body: JSON.stringify({ chainId, asset, amount, routerA, routerB, expectedProfit, agent: 'ARRBY' })
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      return await res.json(); // expected: { advise: boolean, reason?: string }
    } catch (err) {
      console.warn('[mindx_client] preflightAdvise unreachable, failing open:', err.message);
      return { advise: true, reason: 'mindX unreachable — proceeding without advisory signal' };
    }
  }

  /** Log a completed (or reverted) run so mindX can publish it to rage.pythai.net. */
  async logRun({ chainId, txHash, status, asset, profit, routerA, routerB }) {
    try {
      const res = await fetch(`${this.baseUrl}${this.logPath}`, {
        method: 'POST',
        headers: this._headers(),
        body: JSON.stringify({
          source: 'ARRBY',
          chainId, txHash, status, asset, profit, routerA, routerB,
          ts: new Date().toISOString()
        })
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    } catch (err) {
      console.warn('[mindx_client] logRun failed (non-fatal):', err.message);
    }
  }
}

module.exports = { MindXClient };
