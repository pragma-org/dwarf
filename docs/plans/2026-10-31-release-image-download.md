# Release image download implementation plan

Goal: Download a discovered public release's exact OCI image from Versions and select it in Run without changing qualification or defaults.

Design approved in conversation: per-release Download image, visible job status and verified local availability, then Use in a run. Source builds and instrumentation are outside this change.

Architecture: resolve only official catalog identities on both sides of the controller boundary. Reuse authenticated controller background executions, mutation serialization, restart reconciliation, and audited terminal receipts. Retain the dashboard's job handle for reload and deduplication; re-inspect completed images before claiming availability. Share the effective catalog with Run. No browser-supplied registry URL or implicit topology action.

1. Add failing tests for discovered-release selection, download authentication and identity constraints, asynchronous controller success/failure/restart receipts, and browser actions.
2. Implement a shared catalog image resolver, typed controller download operation, authenticated dashboard start/status endpoints, and bounded polling UI.
3. Fix Run catalog loading and exact-version URL selection, retaining experimental consent and normal Prepare gates.
4. Run focused Python and browser regressions; review the diff. Document the workflow.
5. Commit a clean public-history candidate, build both app images, deploy with the existing runtime and port, and exercise the actual latest Amaru download and wizard selection. Preserve rollback image tags and data. Do not start a scenario without a reviewed selection.

Validation before deployment:
- 348 focused Python/browser-invoking checks passed; six existing failures reproduced on unchanged da991675 were excluded from the final targeted run (four landing-wizard fixture/retained-prepare tests and two runtime adapter tests).
- Dedicated browser test passed download progress, one pull, reload/resume, exact version/digest handoff, rejected stale selection, and no topology mutation. Existing Versions browser regression passed.
- Independent review identified platform retry and stale-selection risks; both were corrected and covered by regression checks. Downloaded catalog images are accepted for Prepare only after full repository/digest and local platform verification; qualification is unchanged.
