document.addEventListener("DOMContentLoaded", () => {
  const root = document.querySelector(".measurement-coverage");
  if (!root) return;
  const search = root.querySelector("#measurement-coverage-search");
  const implementation = root.querySelector("#measurement-coverage-implementation");
  const source = root.querySelector("#measurement-coverage-source");
  const status = root.querySelector("#measurement-coverage-status");
  const buttons = [...root.querySelectorAll("[data-coverage-view]")];
  const panels = [...root.querySelectorAll("[data-coverage-panel]")];
  const empty = root.querySelector("#measurement-coverage-empty");
  const resultStatus = root.querySelector("#measurement-coverage-result-status");
  let activeView = buttons[0]?.dataset.coverageView || "threats";

  function applyFilters() {
    const panel = panels.find((item) => item.dataset.coveragePanel === activeView);
    let visible = 0;
    panels.forEach((item) => { item.hidden = item !== panel; });
    if (panel) {
      panel.querySelectorAll(".measurement-coverage__row").forEach((row) => {
        const matches = (!search.value || row.dataset.search.includes(search.value.toLowerCase()))
          && (!implementation.value || row.dataset.implementations.includes(implementation.value))
          && (!source.value || row.dataset.sources.includes(source.value))
          && (!status.value || row.dataset.status === status.value);
        row.hidden = !matches;
        if (matches) visible += 1;
      });
    }
    empty.hidden = visible !== 0;
    resultStatus.textContent = `${visible} entries shown.`;
  }

  buttons.forEach((button) => button.addEventListener("click", () => {
    activeView = button.dataset.coverageView;
    buttons.forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
    applyFilters();
  }));
  [search, implementation, source, status].forEach((control) => {
    control.addEventListener(control === search ? "input" : "change", applyFilters);
  });
  applyFilters();
});
