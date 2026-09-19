# Measurement Report Redesign Design

## Goal

Make a DWARF run measurement report immediately understandable to a non-expert while preserving every retained value, source artifact, download, raw metric identifier, and security-run semantic.

## Design decision

The default run inspector will present a shared client vocabulary rather than implementation-specific result keys. A versioned presentation catalog will bind canonical concepts to Amaru and Cardano-node result fields and declare the source as `stock`, `external`, `patched`, or `reserved`. Raw identifiers remain available in technical disclosures and a separate raw-data view.

The source and run status are independent:

- **Stock** means the signal came from telemetry already emitted by an unmodified node.
- **External** means DWARF observed the real node from outside its process.
- **Patched** means a revision-locked node patch exposed an internal boundary.
- **Reserved / not implemented** means the shared taxonomy contains the concept but no collector implements it for that target.
- **Unavailable** means an implementation exists, but this exact run did not provide the evidence needed to produce a value.

This distinction prevents a missing implementation from being presented as a zero, and prevents a bounded run that did not exercise a boundary from being presented as an implementation gap.

## Architecture

### Presentation catalog

`dwarf/measurements/presentation-v1.json` will define:

- ordered categories;
- canonical concept identifiers;
- human-readable titles and one-sentence descriptions;
- metric type and primary statistic;
- implementation- and mode-aware raw metric bindings;
- source badge and derivation explanation;
- explicitly reserved concepts.

This metadata is presentation-only. It does not alter `measurements/report.json`, report aggregation, thresholds, or measurement semantics.

### Server-side view model

`profile_manager.measurement_presentation` will load and validate the catalog, normalize the retained report into concept cards, merge companion `*_by_outcome` records into their parent card, select the correct primary result, assign evidence-volume labels, and retain a complete raw row representation.

The run data extractor will provide:

- the original report metric count and availability count;
- assertion, collector, and error summaries as distinct concepts;
- ordered category groups;
- searchable concept cards;
- target and source provenance;
- an operator raw table model.

Unknown future metrics will remain visible through a conservative shape-derived fallback card. They will never be dropped because presentation metadata lags collection.

### Primary report

The existing run page will show:

- five clearly labeled summary facts;
- a visible claim boundary;
- a short glossary;
- search and category controls;
- two-column metric cards on desktop and one column on mobile;
- native `details` disclosures for raw identifiers, statistics, derivation, outcome breakdowns, and exact target/source;
- unavailable and reserved concepts without zero-valued placeholders.

Distribution cards lead with median and sample count. Scalar/rate cards lead with the derived value. Count cards lead with an event count. Unavailable and reserved cards lead with their reason.

Evidence labels describe only volume: very small (`n < 5`), small (`n < 30`), or useful (`n >= 30`). They do not claim statistical authority.

### Raw operator view

`/operate/runs/<id>/measurements/raw` will provide the exact operator-oriented table and link back to the concept report. JSON and Markdown download routes remain unchanged. Raw IDs also remain visible within every concept card.

### Interaction and accessibility

Search and category filtering will use an external script shared by the primary and raw views. Category controls will be native buttons with `aria-pressed`; disclosures will use native `details` and `summary`. Hidden groups will not leave empty headings. The layout will constrain long IDs and target digests and prevent page-level horizontal overflow at desktop, tablet, and mobile widths.

## Verification

The implementation will use test-first data-contract and rendered-page tests. The retained Amaru and Cardano-node acceptance runs will then be checked in a browser at 1280px, 768px, and 390px widths. Verification includes filter interaction, disclosure content, scalar/count/distribution behavior, reserved-versus-unavailable behavior, small-sample labels, route/download continuity, and page overflow.

