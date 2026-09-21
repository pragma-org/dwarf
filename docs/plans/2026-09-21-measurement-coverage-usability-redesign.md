# Measurement coverage usability redesign implementation plan

Date: 2026-09-21

1. Add failing data and presentation contracts for overview counts, grouped taps, grouped evidence, bounded result rendering, collapsed disclosures, and human-first summaries.
2. Extend the existing render-time payload with presentation summaries and groups. Preserve the current mapping, evidence rules, and state decisions.
3. Replace the page hierarchy with overview tiles, compact help, sticky controls, collapsed summary cards, and independent nested disclosures.
4. Add bounded “show more” behavior for results and on-demand “show all” behavior for long scenario and evidence lists.
5. Update responsive and print CSS within the existing DWARF theme.
6. Run focused tests, the full suite, mapping/schema/semantic validation, and offline profile renders.
7. Render and visually inspect the four required states at desktop, tablet, and mobile widths. Check keyboard use, links, console output, print mode, and overflow.
8. Review and commit only the intended files. Push internal main, build and deploy that exact commit, and verify the live routes.
9. Reconcile the same public-safe change onto current public main. Run the public safety audit and exact candidate tests, then push normally and verify the remote files.
