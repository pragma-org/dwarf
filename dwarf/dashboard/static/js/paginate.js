/* Client-side paging for the long catalogue pages.
 *
 * Follows the same convention as filter.js: no view changes, no URL state,
 * refresh resets. It sits *on top of* whichever filter a page already uses
 * (filter.js single-select, scenario-filter.js multi-select) rather than
 * replacing either — it only ever hides rows the filter has already left
 * visible, and recomputes whenever the filter changes anything.
 *
 * Used by:
 *   /operate/scenarios   (ul  > li.scenario-row)   243 rows
 *   /operate/primitives  (tbody > tr)              208 rows
 *   /operate/testcases   (tbody > tr)
 *
 * Rows beyond the limit get .is-paged-out (display:none in bento-noir.css).
 * Filter-visibility is measured with that class removed, so the two
 * mechanisms never fight over the same row.
 */
const PAGED_OUT = "is-paged-out";

export function initPaginate({container, rows, pageSize = 50, label = "items"}) {
  const root = document.querySelector(container);
  if (!root) return;
  const all = Array.from(root.querySelectorAll(rows));
  if (all.length <= pageSize) return;          // nothing to page

  let limit = pageSize;

  const bar = document.createElement("div");
  bar.className = "pager";
  bar.innerHTML =
    '<span class="pager__count"></span>' +
    '<span class="pager__actions">' +
      '<button type="button" class="cta cta--ghost" data-more></button>' +
      '<button type="button" class="cta cta--ghost" data-all>Show all</button>' +
    "</span>";
  root.insertAdjacentElement("afterend", bar);

  const countEl = bar.querySelector(".pager__count");
  const moreBtn = bar.querySelector("[data-more]");
  const allBtn  = bar.querySelector("[data-all]");

  function eligible() {
    // measure filter-visibility with our own class lifted
    all.forEach((r) => r.classList.remove(PAGED_OUT));
    return all.filter(
      (r) => !r.hidden && getComputedStyle(r).display !== "none"
    );
  }

  function apply() {
    // our own class writes must not look like a filter change
    mo.disconnect();
    const vis = eligible();
    vis.forEach((r, i) => {
      if (i >= limit) r.classList.add(PAGED_OUT);
    });
    const shown = Math.min(limit, vis.length);
    countEl.textContent =
      vis.length <= shown
        ? `${vis.length} ${label}`
        : `Showing ${shown} of ${vis.length} ${label}`;
    const remaining = vis.length - shown;
    moreBtn.hidden = remaining <= 0;
    allBtn.hidden = remaining <= 0;
    moreBtn.textContent = `Show ${Math.min(pageSize, remaining)} more`;
    bar.hidden = vis.length <= pageSize && limit >= vis.length;
    observe();
  }

  moreBtn.addEventListener("click", () => { limit += pageSize; apply(); });
  allBtn.addEventListener("click", () => { limit = all.length; apply(); });

  // A filter changed something: go back to the first page and re-measure.
  const mo = new MutationObserver(() => {
    if (mo.__busy) return;
    mo.__busy = true;
    requestAnimationFrame(() => { mo.__busy = false; limit = pageSize; apply(); });
  });
  function observe() {
    mo.observe(root, {attributes: true, subtree: true,
                      attributeFilter: ["hidden", "class", "style"]});
    const table = root.closest("table") || root;
    if (table !== root) {
      mo.observe(table, {attributes: true, attributeFilter: [
        "data-active-status", "data-active-implementation", "data-active-family"]});
    }
  }

  apply();
}
