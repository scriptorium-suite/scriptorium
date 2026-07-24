# Changelog

## 0.2.0 (candidate) — 2026-07-22

- Add `scriptorium resume`, backed by Provenance's bounded, read-only Context
  Capsule. It restores approved project state, explicitly labels auto-applied
  low-risk progress, and keeps literature artifacts reference-only.
- Extend the synthetic demo with all four literature-reading artifacts, atomic
  idempotent ingestion, reference hints, and privacy assertions.
- Exercise two consecutive synthetic Agent sessions so the second session proves
  that it can resume the first session's reviewed project state.
- Require compatible `prov-context` and `prov-ingest-research` commands in Public
  Alpha diagnostics, including an actual runtime-version probe.
- Move the candidate compatibility baseline to `scriptorium-spec` 2.3.0 for the
  `experiment-run/1.0` and `claim-evidence/1.0` contracts. Runtime registration,
  persistence, query, human review, and claim linkage remain V0.3 work.
- Add the safety-reviewed `scriptorium migrate` CLI as a V0.3 candidate for
  explicit Markdown/PDF copies: write-free plan, create-if-absent apply,
  canonical private state, cross-process verify/reapply recovery, and rollback.
  Reports and errors remain aggregate-only and path-free; this does not mark V0.3
  complete.
- Add an offline, synthetic Steward-to-Lectern acceptance path that validates a
  two-paper handoff through Lectern's production graph, stops for outline approval
  with no persistent pre-approval PPTX, compiles an editable deck, and
  scans transferable artifacts for paths, email addresses, and credential shapes.
- Add a temporary clean-environment lifecycle gate that builds local wheels with
  package indexes disabled, verifies install/uninstall/reinstall and the synthetic
  demo, and exercises the public v0.1.0-to-current version transition. Live Agent,
  PowerPoint, external-user, and fresh remote-CI acceptance remain explicit gates.

## 0.1.0 — 2026-07-20

- Add `scriptorium inventory`, a deterministic, zero-write preview for explicitly
  selected Markdown/PDF sources, AI conversation exports, and Zotero exports. It
  classifies by suffix without opening content, suppresses paths and filenames,
  rejects unsafe or incomplete roots, and reports aggregate review routes only.
  It does not discover personal folders, persist a manifest, ingest, copy, or apply
  a migration.
- Add the first thin suite entry with `scriptorium demo`.
- Add a credential-free synthetic golden path through the canonical spec validator,
  Steward review workflow, and Provenance ingest/search/MCP APIs. After source
  installation, the path requests no suite-managed runtime network action; source
  installation itself may fetch declared build requirements.
- Add an isolated Markdown workspace, compatibility manifest, machine-readable
  demo report, Windows end-to-end CI, and cross-platform unit tests.
- Add read-only `doctor` targets for Demo and Public Alpha readiness, with stable
  JSON output, explicit remediation and release blockers, egress disclosure, and
  Windows app detection.
- Add write-free-by-default `scriptorium init` for a deterministic, no-clobber
  suite config, separate workspace/data-root directories, and minimal `project/1.0`
  note whose session-resolution root defaults to the workspace; `host install`,
  `doctor`, and `pull` can use the resulting config fallback
  without init installing a host, model, or hook, reading provider credentials, or
  requesting network access.
- Add one canonical `scriptorium-research` Agent Skill, explicit no-clobber Codex
  and Claude Code workspace installers, managed upgrade receipts, and matching
  doctor evidence.
- Add the write-free-by-default `scriptorium pull` control-plane entry over
  Provenance's machine-readable public pull command, including explicit data roots,
  structured action-required reporting, Codex adapter discovery, and capability-based
  doctor evidence without embedding a model or silently installing hooks.
- Add the content-free `scriptorium status` control-plane summary over trusted
  doctor and pull-preview reports. It exposes allowlisted readiness, freshness,
  aggregate backlog counts, and fixed review cues without forwarding paths,
  identifiers, research content, stderr, or an implicit `--run` authorization.
  It authorizes no suite project/data writes while accurately disclosing external
  readiness probes.
- Report content-free path-selection provenance across `doctor`, `pull`, and
  `status`; warn when environment roots conflict with suite config and fail closed
  before `pull --run` unless explicit CLI roots remove the ambiguity. Treat a
  configured but missing `CODEX_HOME` as zero sessions plus setup remediation,
  without creating the directory. Explicit component-root environment variables
  also fail closed instead of silently falling back to a different checkout.
- Preserve local home and temporary-directory variables in the secret-stripped
  diagnostic subprocess environment so installed Windows Provenance entries can
  resolve their profile without inheriting provider credentials.
- Redirect demo `APPDATA` and `LOCALAPPDATA` into the owned demo root so Windows
  subprocesses cannot reuse the user's real application configuration or caches.
- Document verified product-design inspirations, independent implementation boundaries,
  current capability gaps, and the policy for future third-party reuse.
- Wire the canonical research skill to the aggregate `agent-fill` / `project-resolution`
  actions through read-only unresolved inspection, allowlisted scaffold reads, and an
  atomic credential-checked fill command; candidate and authoritative writes require
  separate approval and protected paths are never constructed by the skill.
- Refuse unresolved-project session summaries and preserve those events for explicit,
  recoverable project mapping.
