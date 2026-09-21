document.addEventListener("DOMContentLoaded", () => {
  const root = document.querySelector(".measurement-coverage");
  if (!root) return;

  const INITIAL_PAGE_SIZE = 10;
  const search = root.querySelector("#measurement-coverage-search");
  const implementation = root.querySelector("#measurement-coverage-implementation");
  const source = root.querySelector("#measurement-coverage-source");
  const status = root.querySelector("#measurement-coverage-status");
  const reset = root.querySelector("#measurement-coverage-reset");
  const showMore = root.querySelector("#measurement-coverage-show-more");
  const buttons = [...root.querySelectorAll("[data-coverage-view]")];
  const panels = [...root.querySelectorAll("[data-coverage-panel]")];
  const empty = root.querySelector("#measurement-coverage-empty");
  const resultStatus = root.querySelector("#measurement-coverage-result-status");
  const controls = { q: search, implementation, source, status };
  const validViews = new Set(buttons.map((button) => button.dataset.coverageView));
  const initialState = new URLSearchParams(window.location.search);
  let activeView = validViews.has(initialState.get("view")) ? initialState.get("view") : "threats";
  let visibleLimit = INITIAL_PAGE_SIZE;
  let beforePrintLimit = INITIAL_PAGE_SIZE;

  Object.entries(controls).forEach(([name, control]) => {
    const value = initialState.get(name);
    if (value !== null) control.value = value;
  });

  function matches(template) {
    return (!search.value || template.dataset.search.includes(search.value.toLowerCase()))
      && (!implementation.value || template.dataset.implementations.includes(implementation.value))
      && (!source.value || template.dataset.sources.includes(source.value))
      && (!status.value || template.dataset.status === status.value);
  }

  function writeState() {
    const params = new URLSearchParams();
    if (activeView !== "threats") params.set("view", activeView);
    Object.entries(controls).forEach(([name, control]) => {
      if (control.value) params.set(name, control.value);
    });
    const query = params.toString();
    window.history.replaceState({}, "", `${window.location.pathname}${query ? `?${query}` : ""}`);
  }

  function renderResults({ updateHistory = true } = {}) {
    const panel = panels.find((item) => item.dataset.coveragePanel === activeView);
    panels.forEach((item) => { item.hidden = item !== panel; });
    buttons.forEach((item) => item.setAttribute("aria-pressed", String(item.dataset.coverageView === activeView)));

    if (!panel) return;
    const destination = panel.querySelector(".measurement-coverage__rows");
    const matched = [...panel.querySelectorAll(".measurement-coverage__row-template")].filter(matches);
    const shown = matched.slice(0, visibleLimit);
    destination.replaceChildren(...shown.map((template) => template.content.cloneNode(true)));
    empty.hidden = matched.length !== 0;
    showMore.hidden = shown.length >= matched.length;
    showMore.textContent = `Show ${Math.min(INITIAL_PAGE_SIZE, matched.length - shown.length)} more`;
    resultStatus.textContent = `${shown.length} of ${matched.length} matching entries shown.`;
    if (updateHistory) writeState();
  }

  buttons.forEach((button) => button.addEventListener("click", () => {
    activeView = button.dataset.coverageView;
    visibleLimit = INITIAL_PAGE_SIZE;
    renderResults();
    root.querySelector(".measurement-coverage__explorer")?.scrollIntoView({ block: "start", behavior: "smooth" });
  }));

  Object.values(controls).forEach((control) => {
    control.addEventListener(control === search ? "input" : "change", () => {
      visibleLimit = INITIAL_PAGE_SIZE;
      renderResults();
    });
  });

  reset.addEventListener("click", () => {
    Object.values(controls).forEach((control) => { control.value = ""; });
    activeView = "threats";
    visibleLimit = INITIAL_PAGE_SIZE;
    renderResults();
    search.focus();
  });

  showMore.addEventListener("click", () => {
    visibleLimit += INITIAL_PAGE_SIZE;
    renderResults();
  });

  root.addEventListener("click", (event) => {
    const link = event.target.closest(".measurement-coverage__row-summary a");
    if (link) event.stopPropagation();
    const button = event.target.closest('[data-action="show-all"]');
    if (!button) return;
    const detail = button.closest(".measurement-coverage__detail-section");
    const list = detail.querySelector(".measurement-coverage__initial-list");
    const template = detail.querySelector(".measurement-coverage__more-items");
    if (list && template) list.append(template.content.cloneNode(true));
    template?.remove();
    button.remove();
  });

  window.addEventListener("beforeprint", () => {
    beforePrintLimit = visibleLimit;
    visibleLimit = Number.POSITIVE_INFINITY;
    renderResults({ updateHistory: false });
  });
  window.addEventListener("afterprint", () => {
    visibleLimit = beforePrintLimit;
    renderResults({ updateHistory: false });
  });

  renderResults({ updateHistory: false });
});
