// oracle.js — BANKON chain-oracle widget (shared, identical across the ALGO/ETH twins of BTC.oracle).
// "The clock kept on a block": mesh + shimmer + THROB + interval sparkline, live auto-measured
// new-block/round stream (heartbeat), a block-measurement accordion with verbosity (Quiet→Scientific)
// for per-unit forensics, and JSON/JSONL/CSV export. Reads window.BANKON_ORACLE.
(function () {
  function mount(cfg) {
    const root = document.getElementById('oracle'); if (!root) return;
    root.innerHTML = `<fieldset><legend>${cfg.name} — the clock kept on a ${cfg.word}</legend>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <canvas id="omesh" width="520" height="220" style="flex:2;min-width:300px;background:#04070c;border-radius:8px"></canvas>
        <div id="ostats" style="flex:1;min-width:200px;font-size:.9rem"></div></div>
      <div class="row" style="margin-top:8px;gap:6px;align-items:center">
        <b>📜 ${cfg.word} history</b> <span class="muted">— expand a ${cfg.word} for forensics</span>
        <label style="margin-left:auto">logging
          <select id="overb"><option>Quiet</option><option selected>Normal</option><option>Verbose</option><option>Scientific</option></select></label>
        <label><input type="checkbox" id="oauto" checked/> ⚡ auto-measure</label></div>
      <div id="oacc" style="max-height:34vh;overflow:auto;border:1px solid #2a3142;border-radius:6px;padding:4px"></div>
      <div class="row" style="margin-top:8px;gap:6px;align-items:center">
        <b>🔬 measurement log</b> <span class="muted">— live new-${cfg.word} stream (node heartbeat)</span>
        <button id="oj" class="secondary" style="margin-left:auto">JSON</button><button id="ojl" class="secondary">JSONL</button>
        <button id="ocsv" class="secondary">CSV</button><button id="oclr" class="secondary">clear</button></div>
      <pre id="omlog" style="min-height:90px;max-height:24vh;overflow:auto;background:#05080d">// new ${cfg.word}s stream here as they arrive — visual confirmation the node is connected + feeding</pre></fieldset>`;

    const O = { phase: 0, series: [], head: '—', sub: '' };
    const M = { rows: [], seen: new Set(), accSeen: new Set(), primed: false };
    const cv = root.querySelector('#omesh'), ctx = cv.getContext('2d'), AC = cfg.accent;
    (function draw() {
      requestAnimationFrame(draw);
      const w = cv.width, h = cv.height, step = 16; ctx.fillStyle = '#04070c'; ctx.fillRect(0, 0, w, h);
      ctx.strokeStyle = 'rgba(0,191,255,0.15)'; ctx.lineWidth = 1; ctx.beginPath();
      for (let x = 0; x <= w; x += step) { ctx.moveTo(x, 0); ctx.lineTo(x, h); }
      for (let y = 0; y <= h; y += step) { ctx.moveTo(0, y); ctx.lineTo(w, y); } ctx.stroke();
      const cx = O.phase * (w + 240) - 120;
      for (let x = 0; x <= w; x += step) { const d = Math.abs(x - cx); if (d < 110) { ctx.strokeStyle = 'rgba(150,228,255,' + (0.6 * (1 - d / 110)).toFixed(3) + ')'; ctx.lineWidth = 1.4; ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke(); } }
      if (O.series.length > 1) { const mx = Math.max(...O.series) || 1; ctx.strokeStyle = AC; ctx.lineWidth = 2; ctx.beginPath(); O.series.forEach((v, i) => { const px = 14 + i / (O.series.length - 1) * (w - 28); const py = h - 18 - (v / mx) * (h - 80); i ? ctx.lineTo(px, py) : ctx.moveTo(px, py); }); ctx.stroke(); }
      const throb = 0.5 + 0.5 * Math.sin(O.phase * Math.PI * 4);
      ctx.save(); ctx.textAlign = 'center'; ctx.shadowColor = AC; ctx.shadowBlur = 10 + 28 * throb;
      ctx.fillStyle = '#eef3f8'; ctx.font = 'bold ' + (24 + 5 * throb).toFixed(0) + 'px sans-serif'; ctx.fillText(O.head, w / 2, h / 2 + 6);
      ctx.shadowBlur = 0; ctx.fillStyle = '#8aa0b4'; ctx.font = '10px sans-serif'; ctx.fillText(O.sub, w / 2, h / 2 + 26); ctx.restore();
      O.phase = (O.phase + 0.01) % 1;
    })();

    const dl = (n, t, ty) => { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([t], { type: ty })); a.download = n; a.click(); }, F = cfg.file;
    root.querySelector('#oj').onclick = () => dl(F + '.json', JSON.stringify(M.rows, null, 2), 'application/json');
    root.querySelector('#ojl').onclick = () => dl(F + '.jsonl', M.rows.map(r => JSON.stringify(r)).join('\n'), 'application/x-ndjson');
    root.querySelector('#ocsv').onclick = () => { const keys = [...new Set(M.rows.flatMap(r => Object.keys(r)))]; dl(F + '.csv', [keys.join(',')].concat(M.rows.map(r => keys.map(k => r[k] == null ? '' : r[k]).join(','))).join('\n'), 'text/csv'); };
    root.querySelector('#oclr').onclick = () => { root.querySelector('#omlog').textContent = ''; };

    const esc = s => String(s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
    const tbl = obj => '<table style="width:100%;font-size:.82rem">' + Object.entries(obj).map(([k, v]) => `<tr><td class="muted" style="padding-right:.6rem">${esc(k)}</td><td>${esc(v)}</td></tr>`).join('') + '</table>';
    async function detail(height, body) {
      body.innerHTML = '<span class="muted">loading ' + cfg.word + ' forensics…</span>';
      const d = await fetch('/api/block/' + height).then(r => r.json()).catch(() => null);
      if (!d || !d.ok) { body.innerHTML = '<span class="err">unavailable (node busy)</span>'; return; }
      M.rows.push({ ts: new Date().toISOString(), height: d.height, time: d.time, nTx: d.nTx, ...d.fields, source: 'detail' });
      const lvl = root.querySelector('#overb').value;
      if (lvl === 'Quiet') { body.innerHTML = '<span class="muted">' + Object.entries(d.fields).slice(0, 4).map(([k, v]) => k + '=' + v).join(' · ') + '</span>'; return; }
      let h = tbl(d.fields);
      if (lvl === 'Verbose' || lvl === 'Scientific') h += '<div class="muted">raw →</div><pre style="max-height:200px;overflow:auto">' + esc(JSON.stringify(d.raw, null, 2)) + '</pre>';
      if (lvl === 'Scientific' && d.derived) h += '<div class="muted">derived measures →</div>' + tbl(d.derived);
      body.innerHTML = h;
    }

    async function tick() {
      const oc = ((await fetch('/api/oracle').then(r => r.json()).catch(() => ({}))).oracle) || {};
      const rb = ((await fetch('/api/recentblocks?n=15').then(r => r.json()).catch(() => ({}))).blocks) || [];
      O.head = oc.avgBlockTime ? (oc.avgBlockTime).toFixed(1) + 's' : '—';
      O.sub = 'avg ' + cfg.word + ' time' + (oc.targetBlockTime ? ` · target ${oc.targetBlockTime}s` : '');
      const srt = rb.filter(b => b.time && b.height != null).sort((a, b) => a.height - b.height);
      O.series = []; for (let i = 1; i < srt.length; i++) { const d = srt[i].time - srt[i - 1].time; if (d >= 0) O.series.push(d); }
      const hgt = oc.height != null ? Number(oc.height).toLocaleString() : '—';
      root.querySelector('#ostats').innerHTML =
        `<div>${cfg.word} height: <b style="color:${AC}">${hgt}</b></div><div>avg ${cfg.word} time: <b>${O.head}</b></div>` +
        `<div>target: ${oc.targetBlockTime || '—'} s</div><div>since last: ${oc.timeSinceLastMs != null ? (oc.timeSinceLastMs / 1000).toFixed(1) + ' s' : '—'}</div>` +
        `<div class="muted">chain: ${oc.chain || cfg.chain}</div>`;
      const acc = root.querySelector('#oacc'), auto = root.querySelector('#oauto'), ml = root.querySelector('#omlog'), tmap = {}; srt.forEach(b => tmap[b.height] = b.time);
      for (const b of srt) {
        // measurement-log heartbeat (new arrivals only)
        if (!M.seen.has(b.height)) {
          M.seen.add(b.height);
          if (M.primed && auto.checked) {
            const prev = tmap[b.height - 1], iv = prev ? (b.time - prev) : null, ts = new Date().toISOString().slice(11, 19);
            ml.textContent += `\n[${ts}] ⬢ NEW ${cfg.word} #${Number(b.height).toLocaleString()} · ${b.nTx ?? '?'} txs` + (iv != null ? ` · Δ ${iv}s` : '');
            ml.scrollTop = ml.scrollHeight;
            M.rows.push({ ts: new Date().toISOString(), height: b.height, time: b.time, nTx: b.nTx, interval_s: iv, source: 'auto' });
          }
        }
        // accordion row (newest on top, lazy forensics on expand)
        if (!M.accSeen.has(b.height)) {
          M.accSeen.add(b.height);
          const det = document.createElement('details');
          det.style.cssText = 'border:1px solid #2a3142;border-radius:5px;margin:2px 0;background:#0d1117';
          const when = new Date(b.time * 1000).toISOString().replace('T', ' ').slice(0, 19);
          det.innerHTML = `<summary style="cursor:pointer;padding:5px;font-family:ui-monospace,monospace;font-size:.82rem">#${Number(b.height).toLocaleString()} · ${when} · ${b.nTx ?? '?'} txs</summary><div class="dbody" style="padding:4px 8px 8px"></div>`;
          let loaded = false;
          det.addEventListener('toggle', () => { if (det.open && !loaded) { loaded = true; detail(b.height, det.querySelector('.dbody')); } });
          acc.insertBefore(det, acc.firstChild);
        }
      }
      while (acc.children.length > 60) acc.removeChild(acc.lastChild);
      M.primed = true;
    }
    tick(); setInterval(tick, cfg.poll || 8000);
  }
  if (window.BANKON_ORACLE) document.addEventListener('DOMContentLoaded', () => mount(window.BANKON_ORACLE));
})();
