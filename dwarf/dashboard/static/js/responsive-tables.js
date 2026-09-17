/* Keep dense evidence tables readable on narrow screens.
 *
 * Templates may opt out with .no-responsive-table. Existing wrappers are
 * retained; every other table receives the same scroll-region primitive.
 */
function tableLabel(table) {
  const caption = table.querySelector('caption');
  if (caption?.textContent.trim()) return caption.textContent.trim();
  const section = table.closest('section, article, main');
  const heading = section?.querySelector('h1, h2, h3');
  return heading?.textContent.trim() || 'results';
}

document.querySelectorAll('.shell-main table:not(.no-responsive-table)').forEach((table) => {
  let wrapper = table.parentElement;
  if (!wrapper?.classList.contains('responsive-table')) {
    wrapper = document.createElement('div');
    wrapper.className = 'responsive-table';
    table.before(wrapper);
    wrapper.append(table);
  }
  wrapper.setAttribute('role', 'region');
  wrapper.setAttribute('aria-label', `Scrollable data table: ${tableLabel(table)}`);
  wrapper.tabIndex = 0;
});
