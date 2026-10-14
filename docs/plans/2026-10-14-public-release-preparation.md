# Public release preparation

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prepare the latest Basic-first DWARF source for public review without losing existing public documentation or exposing private history or runtime state.

**Architecture:** Start from public main `7ce7e817d9541f8e3b18782192fd7b4fb4fe679f`. Apply the reviewed source snapshot from `234e8f256100392e85ae765eb65d8a0c91780dc1` as public-history descendants, excluding private operational artifacts. Preserve every existing public documentation and report path.

**Tech Stack:** Git, Python/pytest, shell installer contracts, Docker Compose, existing GitHub release workflow.

---

## 1. Prepare the isolated candidate

- Keep the deployed/source development worktree untouched.
- Import code, definitions, tests, delivery scripts, and public operator guides.
- Exclude runtime state, bundles, private proof captures, private planning records, signing keys, credentials, caches, and local tooling state.
- Do not merge or push private history.

## 2. Update newcomer documentation

- Update `README.md` with the Basic-default journey and local controller.
- Explain explicit version fetching, local binary import, scoped support decisions, and experimental consent.
- Keep historical findings and accepted client-card evidence links intact.
- Add draft release notes with verified behavior and explicit limitations.

## 3. Verify the candidate

- Compare all existing public `docs/`, `dwarf/docs/`, and `reports/` paths against the candidate; no deletion is permitted.
- Require public security reports to retain their original blobs.
- Scan newly exported material for credentials, private keys, runtime archives, private endpoints, and oversized artifacts; distinguish public examples from live credentials.
- Run README/link checks, affected runtime regression suites, and installer/release contract checks.
- Request an independent review before committing the candidate.

## 4. Prepare publication, without publishing

- Retain source provenance, review results, and the public-parent commit identity.
- Confirm credential availability on the authorized publishing host without exposing credentials.
- Do not push a public branch, create a tag/release, publish images, or run the tag-triggered workflow during preparation.
- Before a signed binary release: choose an immutable version, configure the approved signing secret/public-key pair, and verify digest-pinned target/image indexes for both supported architectures.
- Do not replace the real trust root with a development candidate key.
