# Changelog

## 0.1.0 — Unreleased

- Add `scriptorium inventory`, a deterministic, zero-write preview for explicitly
  selected Markdown/PDF sources, AI conversation exports, and Zotero exports. It
  classifies by suffix without opening content, suppresses paths and filenames,
  rejects unsafe or incomplete roots, and reports aggregate review routes only.
  It does not discover personal folders, persist a manifest, ingest, copy, or apply
  a migration.
- Add the first thin suite entry with `scriptorium demo`.
- Add a credential-free, offline synthetic golden path through the canonical
  spec validator, Steward review workflow, and Provenance ingest/search/MCP APIs.
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
