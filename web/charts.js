/* Small SVG charts. Hand rolled, so the repo has no chart dependency and no CDN.
   Every chart states its own n, because a curve over nine decisions and a curve
   over nine hundred look identical otherwise. */

window.VegaCharts = (function () {
  const esc = function (value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  };

  function svg(width, height, body) {
    return '<svg class="chart" viewBox="0 0 ' + width + " " + height + '" role="img">' + body + "</svg>";
  }

  function fmt(value, digits) {
    if (typeof value !== "number" || !isFinite(value)) return "n/a";
    return value.toFixed(digits === undefined ? 3 : digits);
  }

  /* Reliability: claimed probability against measured accuracy. */
  function reliability(container, curve) {
    if (!container) return;
    if (!curve || !curve.length) {
      container.innerHTML = '<p class="muted">No calibration data in this run.</p>';
      return;
    }
    const width = 460, height = 300, pad = 46;
    const plotW = width - pad - 18, plotH = height - pad - 26;
    const x = function (v) { return pad + v * plotW; };
    const y = function (v) { return height - pad - v * plotH; };
    let body = "";
    // grid
    for (let i = 0; i <= 5; i += 1) {
      const v = i / 5;
      body += '<line x1="' + x(v) + '" y1="' + y(0) + '" x2="' + x(v) + '" y2="' + y(1) + '" stroke="#2a323d" stroke-width="1"/>';
      body += '<line x1="' + x(0) + '" y1="' + y(v) + '" x2="' + x(1) + '" y2="' + y(v) + '" stroke="#2a323d" stroke-width="1"/>';
      body += '<text x="' + (x(v)) + '" y="' + (y(0) + 16) + '" fill="#93a1b1" font-size="10" text-anchor="middle">' + v.toFixed(1) + "</text>";
      body += '<text x="' + (x(0) - 8) + '" y="' + (y(v) + 3) + '" fill="#93a1b1" font-size="10" text-anchor="end">' + v.toFixed(1) + "</text>";
    }
    body += '<line x1="' + x(0) + '" y1="' + y(0) + '" x2="' + x(1) + '" y2="' + y(1) + '" stroke="#6ee7b7" stroke-dasharray="4 4" stroke-width="1"/>';
    let total = 0;
    curve.forEach(function (bucket) {
      if (!bucket.n) return;
      total += bucket.n;
      const radius = Math.max(2.5, Math.min(9, 2 + Math.sqrt(bucket.n) / 2));
      body += '<circle cx="' + x(bucket.confidence) + '" cy="' + y(bucket.accuracy) + '" r="' + radius +
        '" fill="#3b82f6" fill-opacity="0.75" stroke="#93c5fd" stroke-width="1"><title>claimed ' +
        esc(fmt(bucket.confidence)) + ", measured " + esc(fmt(bucket.accuracy)) + ", n=" + bucket.n + "</title></circle>";
    });
    body += '<text x="' + (pad + plotW / 2) + '" y="' + (height - 8) + '" fill="#93a1b1" font-size="11" text-anchor="middle">claimed probability</text>';
    body += '<text x="14" y="' + (height / 2) + '" fill="#93a1b1" font-size="11" text-anchor="middle" transform="rotate(-90 14 ' + (height / 2) + ')">measured accuracy</text>';
    container.innerHTML = svg(width, height, body) +
      '<div class="legend"><span><i style="background:#3b82f6"></i>engine (n=' + total + ")</span>" +
      '<span><i style="background:#6ee7b7"></i>perfect calibration</span></div>';
  }

  /* Horizontal bars, one row per item. */
  function bars(container, items, options) {
    const opts = options || {};
    if (!container) return;
    if (!items || !items.length) {
      container.innerHTML = '<p class="muted">Nothing to show.</p>';
      return;
    }
    const max = opts.max || Math.max.apply(null, items.map(function (i) { return i.value || 0; })) || 1;
    let html = '<div class="bars">';
    items.forEach(function (item) {
      const pct = Math.max(0, Math.min(1, (item.value || 0) / max));
      const width = (pct * 100).toFixed(1);
      html += '<div class="bar-row"><span>' + esc(item.label) + "</span>" +
        '<span class="bar-track"><span class="bar-fill' + (item.key ? " key" : "") + '" style="width:' + width + '%"></span></span>' +
        '<span class="pct">' + esc(item.display !== undefined ? item.display : fmt(item.value)) + "</span></div>";
    });
    html += "</div>";
    if (opts.caption) html += '<p class="muted">' + esc(opts.caption) + "</p>";
    container.innerHTML = html;
  }

  /* Counts as bars, for histograms and distributions. */
  function histogram(container, counts, options) {
    const opts = options || {};
    const keys = Object.keys(counts || {});
    if (!keys.length) {
      container.innerHTML = '<p class="muted">Nothing to show.</p>';
      return;
    }
    const items = keys.map(function (key) {
      return { label: key, value: counts[key], display: String(counts[key]) };
    });
    bars(container, items, opts);
  }

  /* Two labelled values side by side, for the headline comparison. */
  function comparison(container, pairs) {
    if (!container) return;
    const width = 460, height = 200, pad = 52;
    const plotW = width - pad - 20, plotH = height - pad - 24;
    const max = Math.max.apply(null, pairs.map(function (p) { return p.value || 0; }).concat([0.01]));
    let body = "";
    pairs.forEach(function (pair, index) {
      const rowH = plotH / pairs.length;
      const yTop = 20 + index * rowH + rowH * 0.18;
      const barH = rowH * 0.5;
      const width_ = Math.max(1, (pair.value / max) * plotW);
      body += '<text x="' + (pad - 10) + '" y="' + (yTop + barH * 0.8) + '" fill="#e6edf3" font-size="12" text-anchor="end">' + esc(pair.label) + "</text>";
      body += '<rect x="' + pad + '" y="' + yTop + '" width="' + width_ + '" height="' + barH + '" fill="' + (pair.color || "#3b82f6") + '" rx="3"/>';
      body += '<text x="' + (pad + width_ + 8) + '" y="' + (yTop + barH * 0.8) + '" fill="#e6edf3" font-size="12" font-family="monospace">' + esc(fmt(pair.value)) + "</text>";
      if (pair.note) {
        body += '<text x="' + pad + '" y="' + (yTop + barH + 14) + '" fill="#93a1b1" font-size="11">' + esc(pair.note) + "</text>";
      }
    });
    container.innerHTML = svg(width, height, body);
  }

  return { reliability: reliability, bars: bars, histogram: histogram, comparison: comparison };
})();
