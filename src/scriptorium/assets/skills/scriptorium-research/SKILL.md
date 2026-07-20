---
name: scriptorium-research
description: Advance an AI4Science project from a vague intuition or existing evidence into reviewable plans, literature artifacts, experiments, papers, and slide handoffs in a Scriptorium Markdown workspace. Use when the user asks to start, resume, investigate, synthesize, plan, review, report, sync, or close out research with Scriptorium, Steward, Provenance, or Lectern, including when scriptorium pull reports agent-fill or project-resolution.
---

# Scriptorium Research

Use Scriptorium as the orchestration layer for a research workflow. Keep Markdown and
versioned contract files as the collaboration surface; treat each component's public
CLI, read-only MCP tools, and owned files as its stable interface.

## Advance the research in a verifiable loop

1. Orient to the workspace.
   - Identify the workspace, project, research question, current stage, and requested
     deliverable. Run `scriptorium doctor --json --workspace <path>` when available.
   - Read the relevant `Projects/*.md` note and existing contract artifacts before
     proposing changes.
   - Query Provenance through `get_current_context`, `get_portfolio`, or
     `search_brain` when available. Treat memory as retrieval context, never as a
     replacement for source files or primary evidence.
2. Frame the next decision.
   - Restate the question, evidence already available, assumptions, constraints, and
     a concrete success check.
   - Build a compact research frame: falsifiable hypothesis or decision, competing
     explanations, an observation that would weaken the preferred explanation, the
     smallest discriminating analysis or experiment, and the intended deliverable.
   - Ask one focused question when a missing answer would materially change the work;
     otherwise state the assumption and continue.
   - Choose the smallest useful next step instead of expanding the project silently.
3. Gather and assess evidence.
   - For literature work, prefer existing `library-kb`, `parsed-paper`,
     `reading-note`, `lineage-graph`, and `review` artifacts. Use Steward's public
     commands and installed literature skills when they fit.
   - Internet literature search is allowed when needed, but send only public search
     terms, titles, identifiers, or other user-approved material. Keep private notes,
     unpublished results, local paths, and raw conversation history local.
   - Separate source-backed facts, user statements, and inference. Never invent a
     citation, result, figure, experiment, or completed capability.
   - Record a source locator, evidence strength, and material counter-evidence for
     each conclusion that will drive a research decision.
4. Produce a reviewable increment.
   - Use the schema validator before claiming a versioned artifact is valid.
   - Use only public CLI, MCP, and file contracts; do not import component internals.
   - If an optional capability such as Zotero, Obsidian, Lectern, PowerPoint, a
     browser extension, or a parser is absent, preserve the core Markdown path and
     report the degradation explicitly.
   - For slides, produce `handoff/1.x` through Steward's public `steward pick`
     path when that profile is available. Run
     `lectern outline <handoff-dir|pdf> --out <outline.json>`, review the saved
     outline, and only after explicit approval run
     `lectern build --from-outline <outline.json> --out <deck.pptx>`. Do not use
     the one-shot `lectern build <source>` path because it auto-approves a newly
     generated outline.
5. Persist without taking ownership from the user.
   - Preserve human-authored Markdown and each component's source of truth. Do not
     overwrite human-owned sections or edit a derived projection as if it were the
     source artifact.
   - If no project exists, present the proposed project identity, `project/1.x`
     frontmatter, and Markdown research brief before creating `Projects/<id>.md`.
     Use Markdown when no ratified JSON contract exists; do not invent a schema.
   - Treat status, stage, next actions, conclusions, and blockers as high-value
     claims. Stage the exact proposed change for approval unless the user has already
     approved that exact write.
   - Prefer append-only, idempotent writes through the owning component. Do not add a
     daemon, schedule, hook, global host setting, credential, or network action as a
     hidden side effect.
6. Close the loop.
   - Report the question advanced, evidence used, artifacts created or changed,
     validation performed, unresolved uncertainty, approvals still needed, and one
     recommended next action.

## Complete pending session writebacks safely

When the user asks to sync or close out work, run `scriptorium pull` with the explicit
workspace and Provenance home in JSON preview mode first. Treat its aggregate actions as
the control signal.

1. Preview and status checks are zero-write operations. Do not infer permission to create
   a project mapping, submit a fill, run the pull, or tick an approval from a request to
   inspect status.
2. If preview reports `project-resolution`, do not create or fill a session summary. Run
   `prov-sync-unresolved --provenance-home <home> --json`; use its opaque IDs by default.
   Use `--show-paths` only after the user agrees to reveal the local cwd in this agent
   session. Present the exact existing-project mapping, or a new project identity and
   Markdown brief, for approval. Rerun pull only after that approved mapping exists.
3. If preview reports `agent-fill`, run
   `prov-sync-pending --provenance-home <home> --json`. For each returned ID, fetch the
   allowlisted sanitized scaffold with
   `prov-sync-pending <summary-id> --provenance-home <home> --json`. Never construct or
   read a pending filesystem path directly, search another data root, or change global
   host configuration.
4. Draft a `summary-fill/1.0` using only facts supported by those sanitized turns. Omit
   unsupported high-value claims. Do not add `summary_id`, `project`, identity overrides,
   unknown fields, or private filesystem values. Do not send scaffold content to browsers,
   search, connectors, or unrelated tools.
5. Before any fill write, show the user the exact pending IDs and proposed fill content,
   and obtain authorization for those candidate writes. Then submit each JSON object on
   standard input to
   `prov-sync-fill <summary-id> --provenance-home <home> --json`. Never write or replace
   `fill.json` directly; stop if the public command rejects the path, credential, schema,
   duplicate target, or worker lock.
6. Rerun the same JSON preview after accepted fills. Obtain separate authorization before
   adding `--run`, because that advances the authoritative sync workflow. Never tick
   `Approvals.md`; a later pull may apply only boxes the user checked.

## Respect capability boundaries

- A model call belongs to the selected agent host and may use its network settings;
  do not describe that call as offline. Local suite commands do not need model keys.
- Invoke `scriptorium pull` only when the installed build exposes it and the user has
  requested the sync action. If it is unavailable, report that capture/sync is not
  available instead of bypassing the single-worker approval path.
- Keep source-specific capture behavior honest: Codex may use an explicit local-log
  scan, while Claude Code may use an opt-in enqueue-only `SessionEnd` hook. Do not
  promise identical automatic capture.
