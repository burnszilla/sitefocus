"""Inline SVG charts for the sitefocus report. Static SVG, styled by classes in
report.css; one small script adds hover tooltips and the now / after toggle.

Colour roles (validated with the dataviz validator, light surface, all-pairs):
sections use categorical slots 1-3 (#2a78d6, #eb6834, #1baf7a) plus neutral
"other"; states use muted grey (now), slot 1 blue (after links) and a blue
ring (with swap). Identity is never colour alone: legends, shapes, labels,
tooltips and the CSV table views carry it too.
"""
import html
import json
import math

W = 720


def _e(x):
    return html.escape(str(x), quote=True)


def _fmt_pct(v):
    if v == 0:
        return "0%"
    if v >= 1:
        return f"{v:.1f}%"
    if v >= 0.1:
        return f"{v:.2f}%"
    return f"{v:.3f}%"


def page_map(rows, top_sections):
    """rows: dicts from pages.csv. top_sections: up to 3 section names."""
    m = {"l": 52, "r": 16, "t": 16, "b": 48}
    S = 560
    w, h = S + m["l"] + m["r"], S + m["t"] + m["b"]
    X = lambda v: m["l"] + v / 100 * S
    Y = lambda v: m["t"] + (100 - v) / 100 * S
    sec_ix = {s: i + 1 for i, s in enumerate(top_sections[:3])}
    out = [f'<svg class="viz pagemap mode-now" viewBox="0 0 {w} {h}" role="img" aria-labelledby="pm-title pm-desc">',
           '<title id="pm-title">Page map: topic score against link score</title>',
           '<desc id="pm-desc">Each dot is a page. Right is more on topic, up holds more internal PageRank. Lines at 50 split the four groups.</desc>']
    for v in (0, 25, 50, 75, 100):
        cls = "grid mid" if v == 50 else "grid"
        out.append(f'<line class="{cls}" x1="{X(v)}" y1="{Y(0)}" x2="{X(v)}" y2="{Y(100)}"/>')
        out.append(f'<line class="{cls}" x1="{X(0)}" y1="{Y(v)}" x2="{X(100)}" y2="{Y(v)}"/>')
        out.append(f'<text class="tick" x="{X(v)}" y="{Y(0) + 18}" text-anchor="middle">{v}</text>')
        out.append(f'<text class="tick" x="{X(0) - 8}" y="{Y(v) + 4}" text-anchor="end">{v}</text>')
    out.append(f'<text class="axis-label" x="{X(50)}" y="{h - 6}" text-anchor="middle">Topic score (closeness to priority pages)</text>')
    out.append(f'<text class="axis-label" transform="translate(14 {Y(50)}) rotate(-90)" text-anchor="middle">Link score (internal PageRank)</text>')
    data = []
    dots, prio = [], []
    for i, r in enumerate(rows):
        x, y, ya = float(r["topic"]), float(r["links"]), float(r.get("links_after") or r["links"])
        k = sec_ix.get(r["section"], 0)
        cls = f"dot s{k}"
        el = f'<circle class="{cls}" data-i="{i}" cx="{X(x):.1f}" cy="{Y(y):.1f}" r="{"4.5" if r.get("priority") == "1" else "2.2"}"/>'
        (prio if r.get("priority") == "1" else dots).append(el)
        data.append([round(X(x), 1), round(Y(y), 1), round(Y(ya), 1), r["title"][:90], r["url"], r["topic"], r["links"],
                     r.get("links_after") or r["links"], r["group"], r["section"], int(r.get("priority") == "1"), r.get("reach", "")])
    ghosts, moves, up, down = [], [], 0, 0
    for i, r in enumerate(rows):
        x, y, ya = float(r["topic"]), float(r["links"]), float(r.get("links_after") or r["links"])
        ghosts.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="{"4.5" if r.get("priority") == "1" else "2.2"}"/>')
        if abs(ya - y) >= 2:
            cls = "mv up" if ya > y else "mv down"
            up += ya > y
            down += ya < y
            moves.append(f'<line class="{cls}" x1="{X(x):.1f}" y1="{Y(y):.1f}" x2="{X(x):.1f}" y2="{Y(ya):.1f}"/>')
    out.append('<g class="ghosts">' + "".join(ghosts) + "</g>")
    out.append('<g class="moves">' + "".join(moves) + "</g>")
    out.append('<g class="dots">' + "".join(dots) + "</g>")
    out.append('<g class="dots prio">' + "".join(p.replace('class="dot', 'class="dot prio-dot') for p in prio) + "</g>")
    for label, x, y, anchor in (("Leaks", 2, 97, "start"), ("Stars", 98, 97, "end"),
                                ("Long tail", 2, 3, "start"), ("Underlinked", 98, 3, "end")):
        out.append(f'<text class="quad" x="{X(x)}" y="{Y(y) + (12 if y > 50 else -4)}" text-anchor="{anchor}">{label}</text>')
    out.append('<circle class="hl" r="7" cx="-20" cy="-20"/>')
    out.append(f'<rect class="hit" x="{m["l"]}" y="{m["t"]}" width="{S}" height="{S}"/>')
    out.append("</svg>")
    legend = ['<div class="legend">']
    for s, k in sec_ix.items():
        legend.append(f'<span><i class="key s{k}"></i>/{_e(s)}/</span>')
    legend.append('<span><i class="key s0"></i>other sections</span>')
    legend.append('<span><i class="key prio-key"></i>priority page</span></div>')
    controls = ('<div class="toggle" role="group" aria-label="Link score shown">'
                '<button type="button" class="on" data-state="now" aria-pressed="true">Now</button>'
                '<button type="button" data-state="after" aria-pressed="false">After the links</button>'
                '<button type="button" data-state="overlay" aria-pressed="false">Before and after</button></div>')
    legend.insert(-1, '<span class="ov-only"><i class="key ghost-key"></i>before</span>'
                      f'<span class="ov-only"><i class="key up-key"></i>moved up ({up:,})</span>'
                      f'<span class="ov-only"><i class="key down-key"></i>moved down ({down:,})</span>')
    safe = json.dumps(data).replace("</", "<\\/")
    payload = '<script type="application/json" id="pm-data">' + safe + '</script>'
    return f'<figure class="chart">{controls}{"".join(legend)}<div class="viz-wrap">{"".join(out)}</div>{payload}' \
           '<figcaption>Hover a dot for the page. Larger dark dots are priority pages. "After the links" moves every page to its link score after the plan; "Before and after" overlays both, with a line for every page that moves by 2 points or more. A flat row of dots is a set of pages with identical internal PageRank, usually templated pages that are only linked from their directory.</figcaption></figure>'


def dumbbell(items, title, log=False, value_label="share of internal PageRank"):
    """items: [(label, now, after, swap)] as fractions (0-1). Horizontal dumbbells."""
    m = {"l": 230, "r": 130, "t": 34, "b": 48}
    row = 34
    pw = W - m["l"] - m["r"]
    h = m["t"] + m["b"] + row * len(items)
    vals = [v for it in items for v in it[1:] if v is not None and v > 0]
    if log:
        lo = 10 ** math.floor(math.log10(min(vals) * 100))
        hi = 10 ** math.ceil(math.log10(max(vals) * 100))
        X = lambda v: m["l"] + (math.log10(max(v * 100, lo)) - math.log10(lo)) / (math.log10(hi) - math.log10(lo)) * pw
        ticks = [lo * 10 ** k for k in range(int(round(math.log10(hi / lo))) + 1)]
    else:
        hi = max(vals) * 100
        step = 5 if hi <= 40 else 10 if hi <= 80 else 20
        hi = math.ceil(hi / step) * step
        X = lambda v: m["l"] + v * 100 / hi * pw
        ticks = list(range(0, int(hi) + 1, step))
    out = [f'<svg class="viz dumbbell" viewBox="0 0 {W} {h}" role="img" aria-label="{_e(title)}">']
    for t in ticks:
        x = X(t / 100)
        out.append(f'<line class="grid" x1="{x:.1f}" y1="{m["t"] - 6}" x2="{x:.1f}" y2="{h - m["b"] + 4}"/>')
        out.append(f'<text class="tick" x="{x:.1f}" y="{h - m["b"] + 20}" text-anchor="middle">{_fmt_pct(t)}</text>')
    for i, (label, now, after, swap) in enumerate(items):
        y = m["t"] + row * i + row / 2
        tip = f"{label}|Now: {_fmt_pct(now * 100)}|After the links: {_fmt_pct(after * 100)}" + (f"|With the swap: {_fmt_pct(swap * 100)}" if swap is not None else "")
        out.append(f'<g class="db-row" tabindex="0" data-tip="{_e(tip)}">')
        out.append(f'<rect class="row-hit" x="0" y="{y - row / 2:.1f}" width="{W}" height="{row}"/>')
        out.append(f'<text class="row-label" x="{m["l"] - 10}" y="{y + 4:.1f}" text-anchor="end">{_e(label[:34])}</text>')
        xs = [X(v) for v in (now, after, swap) if v is not None]
        out.append(f'<line class="db-line" x1="{min(xs):.1f}" y1="{y:.1f}" x2="{max(xs):.1f}" y2="{y:.1f}"/>')
        out.append(f'<circle class="db-now" cx="{X(now):.1f}" cy="{y:.1f}" r="5"/>')
        if swap is not None:
            out.append(f'<circle class="db-swap" cx="{X(swap):.1f}" cy="{y:.1f}" r="6.5"/>')
        out.append(f'<circle class="db-after" cx="{X(after):.1f}" cy="{y:.1f}" r="5"/>')
        end = max(xs)
        out.append(f'<text class="db-val" x="{end + 10:.1f}" y="{y + 4:.1f}">{_fmt_pct(now * 100)} → {_fmt_pct(after * 100)}</text>')
        out.append("</g>")
    out.append(f'<text class="axis-label" x="{m["l"] + pw / 2:.1f}" y="{h - 4}" text-anchor="middle">{_e(value_label)}{" (log scale)" if log else ""}</text>')
    out.append("</svg>")
    legend = ('<div class="legend"><span><i class="key db-now-key"></i>now</span>'
              '<span><i class="key db-after-key"></i>after the links</span>'
              + ('<span><i class="key db-swap-key"></i>with the sitewide swap</span>' if any(it[3] is not None for it in items) else "")
              + "</div>")
    return f'<figure class="chart">{legend}<div class="viz-wrap">{"".join(out)}</div><figcaption>{_e(title)}. Hover a row for exact values.</figcaption></figure>'


def bars(items, title, value_fmt, axis_label, baseline_first=True):
    """items: [(label, value, tip)]. Horizontal bars on one axis; the first row
    (the current state) is drawn in the neutral 'now' colour."""
    m = {"l": 300, "r": 70, "t": 12, "b": 44}
    row = 30
    pw = W - m["l"] - m["r"]
    h = m["t"] + m["b"] + row * len(items)
    hi = max(v for _, v, _ in items) or 1
    lo = min(v for _, v, _ in items)
    # Zoom the axis when all values sit far from zero, and say so.
    start = 0
    if lo > 0.5 * hi:
        start = math.floor(lo * 0.9)
    X = lambda v: m["l"] + (v - start) / (hi - start) * pw
    out = [f'<svg class="viz bars" viewBox="0 0 {W} {h}" role="img" aria-label="{_e(title)}">']
    for k in range(5):
        v = start + (hi - start) * k / 4
        out.append(f'<line class="grid" x1="{X(v):.1f}" y1="{m["t"]}" x2="{X(v):.1f}" y2="{h - m["b"] + 4}"/>')
        out.append(f'<text class="tick" x="{X(v):.1f}" y="{h - m["b"] + 20}" text-anchor="middle">{value_fmt(v)}</text>')
    for i, (label, v, tip) in enumerate(items):
        y = m["t"] + row * i
        cls = "bar now" if (i == 0 and baseline_first) else "bar"
        out.append(f'<g class="db-row" tabindex="0" data-tip="{_e(label + "|" + value_fmt(v) + ("|" + tip if tip else ""))}">')
        out.append(f'<rect class="row-hit" x="0" y="{y:.1f}" width="{W}" height="{row}"/>')
        out.append(f'<text class="row-label" x="{m["l"] - 10}" y="{y + row / 2 + 4:.1f}" text-anchor="end">{_e(label[:44])}</text>')
        out.append(f'<rect class="{cls}" x="{m["l"]}" y="{y + 7:.1f}" width="{max(X(v) - m["l"], 1):.1f}" height="{row - 14}" rx="4"/>')
        out.append(f'<text class="db-val" x="{X(v) + 8:.1f}" y="{y + row / 2 + 4:.1f}">{value_fmt(v)}</text></g>')
    note = f" (axis starts at {value_fmt(start)})" if start else ""
    out.append(f'<text class="axis-label" x="{m["l"] + pw / 2:.1f}" y="{h - 4}" text-anchor="middle">{_e(axis_label + note)}</text></svg>')
    return f'<figure class="chart"><div class="viz-wrap">{"".join(out)}</div><figcaption>{_e(title)}. Hover a row for details.</figcaption></figure>'


SCRIPT = r"""<div class="tooltip" id="tt" role="status" aria-live="polite" hidden></div>
<script>
(function () {
  var tt = document.getElementById('tt');
  function show(lines, ev) {
    tt.replaceChildren();
    lines.forEach(function (t, i) {
      var d = document.createElement('div');
      d.className = i === 0 ? 'tt-head' : 'tt-row';
      d.textContent = t;
      tt.appendChild(d);
    });
    tt.hidden = false;
    var x = ev.clientX + 14, y = ev.clientY + 14;
    var r = tt.getBoundingClientRect();
    if (x + r.width > window.innerWidth - 8) x = ev.clientX - r.width - 14;
    if (y + r.height > window.innerHeight - 8) y = ev.clientY - r.height - 14;
    tt.style.left = x + 'px';
    tt.style.top = y + 'px';
  }
  function hide() { tt.hidden = true; }
  document.querySelectorAll('[data-tip]').forEach(function (g) {
    g.addEventListener('pointermove', function (ev) { show(g.dataset.tip.split('|'), ev); });
    g.addEventListener('pointerleave', hide);
    g.addEventListener('focus', function () {
      var b = g.getBoundingClientRect();
      show(g.dataset.tip.split('|'), { clientX: b.left + b.width / 2, clientY: b.top });
    });
    g.addEventListener('blur', hide);
  });
  var pm = document.querySelector('svg.pagemap');
  if (!pm) return;
  var data = JSON.parse(document.getElementById('pm-data').textContent);
  var state = 'now';
  var dots = pm.querySelectorAll('circle.dot');
  var hl = pm.querySelector('.hl');
  var hit = pm.querySelector('.hit');
  function yOf(d) { return state === 'now' ? d[1] : d[2]; }
  function setMode() { pm.classList.remove('mode-now', 'mode-after', 'mode-overlay'); pm.classList.add('mode-' + state); document.querySelectorAll('.ov-only').forEach(function (e) { e.hidden = state !== 'overlay'; }); }
  setMode();
  hit.addEventListener('pointermove', function (ev) {
    var p = pm.createSVGPoint(); p.x = ev.clientX; p.y = ev.clientY;
    var q = p.matrixTransform(pm.getScreenCTM().inverse());
    var best = -1, bd = 196;
    for (var i = 0; i < data.length; i++) {
      var dx = data[i][0] - q.x, dy = yOf(data[i]) - q.y, dd = dx * dx + dy * dy - (data[i][10] ? 40 : 0);
      if (dd < bd) { bd = dd; best = i; }
    }
    if (best < 0) { hide(); hl.setAttribute('cx', -20); return; }
    var d = data[best];
    hl.setAttribute('cx', d[0]); hl.setAttribute('cy', yOf(d));
    show([d[3] || d[4], 'Topic ' + d[5] + ' · Links ' + (state === 'now' ? d[6] : d[7]) + (state === 'now' ? '' : ' (was ' + d[6] + ')') + (d[11] !== '' ? ' · Harmonic centrality ' + d[11] : ''),
          'Group: ' + d[8] + (d[10] ? ' · priority page' : ''), '/' + d[9] + '/', d[4]], ev);
  });
  hit.addEventListener('pointerleave', function () { hide(); hl.setAttribute('cx', -20); });
  document.querySelectorAll('.toggle button').forEach(function (b) {
    b.addEventListener('click', function () {
      state = b.dataset.state;
      document.querySelectorAll('.toggle button').forEach(function (o) {
        o.classList.toggle('on', o === b); o.setAttribute('aria-pressed', o === b ? 'true' : 'false');
      });
      dots.forEach(function (c) { c.setAttribute('cy', yOf(data[+c.dataset.i])); });
      setMode();
    });
  });
})();
</script>"""
