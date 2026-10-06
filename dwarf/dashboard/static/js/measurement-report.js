const report = document.querySelector('[data-measurement-report]');

if (report) {
  const search = report.querySelector('[data-measurement-search]');
  const filters = [...report.querySelectorAll('[data-measurement-category]')];
  const cards = [...report.querySelectorAll('[data-measurement-card]')];
  const groups = [...report.querySelectorAll('[data-measurement-group]')];
  const empty = report.querySelector('[data-measurement-empty]');
  const expandAll = report.querySelector('[data-measurement-expand-all]');
  const collapseAll = report.querySelector('[data-measurement-collapse-all]');
  let category = 'all';

  const apply = () => {
    const query = (search?.value || '').trim().toLowerCase();
    let visible = 0;
    cards.forEach((card) => {
      const categoryMatches = category === 'all' || card.dataset.measurementCategoryId === category;
      const searchMatches = !query || (card.dataset.measurementSearchText || '').toLowerCase().includes(query);
      card.hidden = !(categoryMatches && searchMatches);
      if (!card.hidden) visible += 1;
    });
    groups.forEach((group) => {
      const hasMatch = [...group.querySelectorAll('[data-measurement-card]')].some((card) => !card.hidden);
      group.hidden = !hasMatch;
      if (hasMatch && (query || category !== 'all')) group.open = true;
    });
    if (empty) empty.hidden = visible !== 0;
  };

  search?.addEventListener('input', apply);
  filters.forEach((filter) => {
    filter.addEventListener('click', () => {
      category = filter.dataset.measurementCategory || 'all';
      filters.forEach((candidate) => {
        const selected = candidate === filter;
        candidate.classList.toggle('is-active', selected);
        candidate.setAttribute('aria-pressed', String(selected));
      });
      apply();
    });
  });
  expandAll?.addEventListener('click', () => {
    groups.filter((group) => !group.hidden).forEach((group) => { group.open = true; });
  });
  collapseAll?.addEventListener('click', () => {
    groups.forEach((group) => { group.open = false; });
  });
}

const rawReport = document.querySelector('[data-measurement-raw]');
if (rawReport) {
  const search = rawReport.querySelector('[data-raw-metric-search]');
  const rows = [...rawReport.querySelectorAll('[data-raw-metric-row]')];
  const empty = rawReport.querySelector('[data-raw-metric-empty]');
  search?.addEventListener('input', () => {
    const query = search.value.trim().toLowerCase();
    let visible = 0;
    rows.forEach((row) => {
      row.hidden = Boolean(query && !(row.dataset.rawMetricSearchText || '').toLowerCase().includes(query));
      if (!row.hidden) visible += 1;
    });
    if (empty) empty.hidden = visible !== 0;
  });
}


/* measurement overview v2: trading-card category filter + per-card distribution detail */
(function () {
  var viz = document.querySelector('[data-mviz]');
  var report = document.querySelector('[data-measurement-report]');
  if (!viz || !report) return;

  function esc(s) { return (window.CSS && CSS.escape) ? CSS.escape(s) : s; }
  function filterBtn(cat) { return report.querySelector('[data-measurement-category="' + esc(cat) + '"]'); }

  // (1) category trading cards -> drive the existing report filter
  var tcards = [].slice.call(viz.querySelectorAll('[data-mviz-cat]'));
  tcards.forEach(function (c) {
    c.addEventListener('click', function () {
      var cat = c.getAttribute('data-mviz-cat');
      var pressed = c.getAttribute('aria-pressed') === 'true';
      tcards.forEach(function (x) { x.setAttribute('aria-pressed', 'false'); });
      var btn = pressed ? filterBtn('all') : (c.setAttribute('aria-pressed', 'true'), filterBtn(cat));
      if (btn) btn.click();
      // the anchor href handles the smooth scroll to the report heading
    });
  });

  // (2) per-card distribution detail
  var detail = viz.querySelector('[data-mviz-detail]');
  var empty = viz.querySelector('[data-mviz-detail-empty]');
  var dTitle = viz.querySelector('[data-mviz-detail-title]');
  var dValue = viz.querySelector('[data-mviz-detail-value]');
  var dBody = viz.querySelector('[data-mviz-detail-body]');
  var closeBtn = viz.querySelector('[data-mviz-detail-close]');
  var selected = null;
  if (!detail) return;

  function num(v) {
    if (v == null) return null;
    v = String(v).replace(/,/g, '').match(/-?\d+(\.\d+)?([eE][-+]?\d+)?/);
    if (!v) return null; var n = parseFloat(v[0]); return isFinite(n) ? n : null;
  }
  function fmt(n) {
    if (n == null) return '—';
    var a = Math.abs(n);
    if (a !== 0 && (a < 0.001 || a >= 100000)) return n.toExponential(2);
    return (Math.round(n * 1000) / 1000).toString();
  }
  function esch(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (m) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m]; }); }

  function clearDetail() {
    if (selected) { selected.classList.remove('is-mviz-selected'); selected = null; }
    detail.hidden = true; if (empty) empty.hidden = false;
  }
  if (closeBtn) closeBtn.addEventListener('click', clearDetail);

  function pbar(marks, unit) {
    var vals = marks.filter(function (m) { return m.v != null; }).map(function (m) { return m.v; });
    if (!vals.length) return '';
    var max = Math.max.apply(null, vals), min = 0;
    if (max <= min) max = min + 1;
    function pos(v) { return ((v - min) / (max - min) * 100); }
    var fillTo = pos(Math.max.apply(null, vals)).toFixed(1);
    var h = '<div class="dk-pbar"><div class="dk-pbar__track"></div>' +
            '<div class="dk-pbar__fill" style="left:0;width:' + fillTo + '%"></div>';
    marks.forEach(function (m) {
      if (m.v == null) return;
      var x = pos(m.v).toFixed(1);
      h += '<div class="dk-pbar__mark ' + (m.cls || '') + '" style="left:' + x + '%"></div>';
      h += '<div class="dk-pbar__cap" style="left:' + x + '%">' + m.k + '</div>';
      h += '<div class="dk-pbar__lab" style="left:' + x + '%">' + fmt(m.v) + '</div>';
    });
    h += '</div><p class="dk-mviz__detail-note">Across observed samples' + (unit ? (' (' + esch(unit) + ')') : '') +
         '. Markers: median &middot; p95 &middot; p99.</p>';
    return h;
  }

  function select(card) {
    var title = card.getAttribute('data-m-title') || 'Measurement';
    var val = card.getAttribute('data-m-value');
    var unit = card.getAttribute('data-m-unit') || '';
    var label = card.getAttribute('data-m-label') || '';
    var median = num(val);
    var mean = num(card.getAttribute('data-m-mean'));
    var p95 = num(card.getAttribute('data-m-p95'));
    var p99 = num(card.getAttribute('data-m-p99'));
    var samples = card.getAttribute('data-m-samples');

    if (selected) selected.classList.remove('is-mviz-selected');
    selected = card; card.classList.add('is-mviz-selected');

    dTitle.textContent = title;
    dValue.innerHTML = (label ? ('<small>' + esch(label) + '</small> ') : '') +
      esch(val || '—') + (unit ? (' <small>' + esch(unit) + '</small>') : '');

    if (p95 != null || p99 != null) {
      var stat = [];
      if (mean != null) stat.push('mean ' + fmt(mean) + (unit ? ' ' + esch(unit) : ''));
      if (samples) stat.push('n = ' + esch(samples) + ' samples');
      dBody.innerHTML = pbar([
        { k: 'median', v: median, cls: '' },
        { k: 'p95', v: p95, cls: 'is-p95' },
        { k: 'p99', v: p99, cls: 'is-p99' }
      ], unit) + (stat.length ? ('<p class="dk-mviz__detail-note">' + stat.join(' &middot; ') + '</p>') : '');
    } else {
      dBody.innerHTML = '<p class="dk-mviz__detail-note">Single value' +
        (samples ? (' &middot; n = ' + esch(samples) + ' samples') : '') + ' &mdash; not a sampled distribution.</p>';
    }
    detail.hidden = false; if (empty) empty.hidden = true;
  }

  report.addEventListener('click', function (e) {
    var card = e.target.closest('[data-measurement-card][data-m-value]');
    if (!card || !report.contains(card)) return;
    if (e.target.closest('a, summary, button')) return;   // don't hijack links / details toggle
    select(card);
    viz.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  });
})();
