# Measurement coverage usability redesign

Date: 2026-09-21
Status: Approved design

## Problem

The current page preserves the correct evidence join, but it presents that join as one large technical dump. It renders every result, puts raw identifiers before human names, repeats evidence for each tap, and opens all technical subjects through one disclosure. A browser can report no horizontal overflow while the page is still difficult to read.

## Design decision

Keep the versioned mapping and the render-time authoritative join. Add a presentation view model that summarizes and groups that data. Do not add a second coverage table.

The first screen has a short purpose statement, four view tiles, a compact status key, and a collapsed “How to read this page” disclosure. Each view tile shows mapped, verified, and gap counts and selects its view.

The explorer shows at most 10 matching entries at first. Search and filters update the matching result set. “Show 10 more” reveals the next bounded group. Reset returns to the Threats view and clears all filters.

Each result is a collapsed summary. It contains the human title and ID, one sentence of meaning, status, implementation badges, a tap summary, scenario count, a short coverage statement, and relevant detail links. Raw tap, scenario, run, and evidence identifiers do not appear in the collapsed summary.

Opening a result does not dump every detail. Five nested disclosures remain closed: Measurement taps, Scenarios, Evidence, Workload prerequisite, and Claims and limits. Tap rows are grouped by client concept. Human names lead; raw IDs sit inside secondary technical disclosures. Scenario and evidence lists show three records initially and store the remainder in inert templates until the user selects “Show all.” Evidence is grouped once per retained run.

## Responsive and accessible behavior

Desktop uses one or two readable card columns. Tablet and mobile use one column and full-width controls. Native buttons and `details` elements provide keyboard behavior. Live result text reports shown and matching counts. Focus styles, readable type, print rules, and state labels remain available without depending on color.

## Claim boundary

This change does not alter applicability, configured, exercised, verified, unavailable, or reserved semantics. It does not convert a zero value or catalog entry into evidence. Known gaps TM-030, TM-031, and RR-027 remain visible.

## Verification

Presentation contracts cover collapsed defaults, hidden raw IDs, bounded results and lists, heading order, names, state changes, and print behavior. Browser checks cover default, filtered, taps-only, and evidence-only states at desktop, tablet, and mobile sizes, including links, console errors, keyboard use, and overflow.
