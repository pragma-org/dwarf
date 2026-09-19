const report = document.querySelector('[data-measurement-report]');

if (report) {
  const search = report.querySelector('[data-measurement-search]');
  const filters = [...report.querySelectorAll('[data-measurement-category]')];
  const cards = [...report.querySelectorAll('[data-measurement-card]')];
  const groups = [...report.querySelectorAll('[data-measurement-group]')];
  const empty = report.querySelector('[data-measurement-empty]');
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
      group.hidden = ![...group.querySelectorAll('[data-measurement-card]')].some((card) => !card.hidden);
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
