# Run Report Collapsible Sections Design

## Goal

Make dense DWARF run reports easier to scan by collapsing measurement categories and secondary evidence panels by default without hiding the run's verdict, warnings, or summary.

## Information hierarchy

The run identity, status banner, topology/precondition warnings, explanatory guide, and summary tiles remain visible. They answer the first questions an operator has: what ran, whether it passed, whether the run was valid, and whether its evidence is trustworthy.

All detailed evidence below that summary becomes an accessible native disclosure. This includes the manifest, resolved versions, execution definition, interesting evidence, tamper and attestation chains, SARIF, replay and diff results, operator actions, cross-implementation reports, substrate evidence, floor preview, assertions, probes, and log tail. Each closed summary retains its title and the most useful count or verdict where one exists.

The measurement report retains its visible summary, claim boundary, glossary, filters, downloads, and collector status. Its six metric categories become native disclosures that are closed by default. Their closed summaries show the category description and concept count. Compact Expand all and Collapse all controls affect measurement categories only. Search or category filtering automatically opens groups containing matches so filtered results cannot remain hidden.

## Interaction and accessibility

Use native `<details>` and `<summary>` elements so keyboard operation and disclosure state work without JavaScript. JavaScript is limited to measurement filtering and bulk expand/collapse. Summary focus states use the existing forensic-noir design tokens.

Nested technical-detail disclosures inside metric cards and evidence tiles remain unchanged. No measurement semantics, evidence, routes, or downloads change.

## Responsive behavior

Closed disclosures are compact at every viewport. Expanded measurement cards remain two columns on desktop and one column on mobile. Tables continue using their existing responsive wrappers and rules. The disclosure summary layout stacks on narrow screens and must not introduce page-level horizontal overflow.

## Verification

Add presentation-contract tests before implementation, observe them fail, then implement the minimal template, CSS, and JavaScript changes. Verify the complete repository suite and exercise both retained Amaru and Cardano acceptance runs at desktop and mobile widths, including default closed state, individual toggling, bulk controls, filtering, nested details, downloads, and overflow.
