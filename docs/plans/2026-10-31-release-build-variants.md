# Release build variants

Goal: prepare two independently identified variants of one selected official source release from Versions: unmodified stock with passive measurement checks, and instrumented with passive plus compiled-instrumentation checks.

The user confirmed that active taps means compiled instrumentation. Both outputs are source builds; downloaded official OCI artifacts remain separate upstream artifacts. Progress, logs, exact source/toolchain/patch/executable/image identities, and per-variant failures are retained. A completed compile does not assert measurement compatibility or scenario qualification. Unsupported instrumentation must be visible and must not prevent the stock result from being retained. Existing version/default/unknown-consent gates remain.

Architecture: controller-owned, authenticated background preparation jobs use explicit immutable catalog source and artifact identities. Build recipes and patch manifests come from trusted DWARF code; browser input only selects release identity. Compilers run in bounded disposable builder containers without the Docker socket or installation credentials. Outputs are registered as distinct exact artifacts and feed the normal Run preparation flow. Use isolated storage/cache and preserve unrelated containers/evidence.

Implementation tasks:
1. Fix image availability independently of download receipts. Verify exact platform/digest through Docker; distinguish missing from failed inspection. Add failing tests and regression browser proof.
2. Add release build planning and typed controller operation. Resolve fixed official source, supported stock recipe and compatible audited instrumentation recipe. Retain independent stock/instrumented job status, stable handles, duplicate prevention, logs and restart interruption. Test invalid identity, stale selection, failure isolation, and no qualification promotion.
3. Implement isolated acquisition/stock/instrumented execution using existing trusted builders where applicable. Validate source, patches, toolchain, executable/interface and image identities; retain capability checks separately from build status. Missing recipes remain blocked, never fabricated. Exercise actual supported paths on cardano-box.
4. Add two-variant UI, authenticated polling and exact Run handoff; preserve download-only compatibility and local availability semantics. Add browser tests for successful, partial and failed preparation and page reload.
5. Review, run focused checks, build clean committed app images, deploy preserving existing port/runtime/credentials and old image tags. Verify live workflow, local availability, both variant identity selection and honest tap readiness.
