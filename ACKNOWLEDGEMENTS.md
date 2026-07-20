# Design inspirations and acknowledgements

[中文](ACKNOWLEDGEMENTS.zh.md)

This document is non-normative. It records public work that informed Scriptorium's
product thinking and makes the boundary between inspiration and implementation
explicit. The current capability boundary is defined by the README, reproducible
tests, and the versioned contracts in `scriptorium-spec`.

Scriptorium is independently implemented. The references below are not bundled
dependencies, and their inclusion does not imply code reuse, feature equivalence,
integration, sponsorship, endorsement, or affiliation.

## Product thesis shaped by this review

Scriptorium keeps a deliberately narrower role than an autonomous scientist or a
single all-in-one research application:

- **Continuity over autonomy.** Preserve enough accepted project state that the next
  agent session can resume useful work without replaying the entire chat history.
- **Evidence over fluency.** Model output is a candidate until its source and review
  state are visible.
- **Explicit gates over hidden automation.** Preview, run, agent fill, and human
  approval are separate transitions.
- **Files remain authoritative.** Markdown, PDFs, code, and versioned exchange files
  remain useful without a proprietary database or a specific agent host.
- **Delivery derives from accepted state.** Manuscript and slide workflows should
  carry the checkpoint, claims, sources, skill version, and review state they used.
- **Methods live in skills; trust lives in the core.** Skills encode research methods.
  Components own deterministic validation, provenance, privacy, and write boundaries.

## Verified primary references

| Public work | Mechanism studied | Scriptorium adaptation and current boundary |
|---|---|---|
| [ResearchLoop](https://github.com/plan-lab-szu/ResearchLoop) and its [technical report](https://arxiv.org/abs/2605.28282) | Durable research state, evidence-gated claim admission, closeout, and manuscript binding | Scriptorium already separates append-only timeline facts from human-gated high-value claims. It does **not** yet provide a complete research-question-to-evidence-to-manuscript ledger. |
| [SNL-UCSB literature-survey-skill](https://github.com/SNL-UCSB/literature-survey-skill) | Intent, triage, calibrated reading depth, synthesis, corpus expansion, bias checks, comparison matrices, and dependency analysis | Scriptorium has staged `reading-note`, `review`, and `lineage-graph` contracts. Explicit survey intent, time budgets, invariant matrices, and corpus-growth approval remain candidate improvements. Scriptorium uses its own file and privacy boundaries rather than NotebookLM as a required backend. |
| [paperpipe](https://github.com/hummat/paperpipe) | Local separation of PDFs, source LaTeX, equations, summaries, notes, and figures; agent access through public tools | Steward and the contracts already separate original and derived artifacts. Equation-level implementation verification and a complete local implementation-oriented paper database are not current suite capabilities. |
| [SignalGraph](https://github.com/zhiliscope/SignalGraph) | Explainable relations that retain their source sentence, with extraction separated from graph storage | `lineage-graph` edges already carry evidence and use a versioned file contract. Scriptorium does not claim to provide a general research knowledge graph or comprehensive relation extraction. |
| [PaperSpine](https://github.com/WUBING2023/PaperSpine) | Contribution-first writing, results-to-contribution mapping, recoverable staged work, and reviewer-aware gates | These principles inform a later manuscript and delivery gate. Scriptorium does not currently claim a complete manuscript pipeline, reviewer validation, or publication readiness. |
| [Citation Check](https://github.com/serenakeyitan/citation-check-skill) | Two-pass claim extraction and verification, precise numeric checks, and distinct verification outcomes | Human approval in Scriptorium is not citation verification. A future delivery gate should record claim-to-source support, numerical consistency, contradictions, and unresolved items before an artifact is called verified. |
| [Academic Research Skills](https://github.com/Imbad0202/academic-research-skills) and its [architecture](https://github.com/Imbad0202/academic-research-skills/blob/main/docs/ARCHITECTURE.md) | Human checkpoints, integrity gates, material passports, and staged research/write/review workflows | Scriptorium studies these mechanisms without adopting the project's large multi-agent architecture. A shared artifact manifest and explicit delivery gates are future work. |
| [Anthropic Skill Creator](https://github.com/anthropics/skills/tree/main/skills/skill-creator) | Progressive disclosure plus iterative skill evaluation against baselines, objective assertions, and human review | Scriptorium ships one canonical cross-host research skill and managed installers. A public with-skill/baseline evaluation harness and maturity evidence are still missing. |

Additional references for optional profiles include
[PaperQA2](https://github.com/Future-House/paper-qa) for scientific retrieval,
[Nature Skills](https://github.com/Yuan1z0825/nature-skills) for skill packaging and
maturity labels, [Speaker](https://github.com/AI272/speaker) for evidence-aware speaker
notes over real PPTX files, and
[SciPilot Figure Skill](https://github.com/Haojae/scipilot-figure-skill) for data-first
figure selection and visual QA. These are possible adapters or methods, not current
core dependencies or capability claims.

The name “MycEvo” from the initial research notes was not used here because no unique
official source could be verified. ResearchLoop is cited by its verified project name;
the two names should not be treated as aliases without primary evidence.

## How the lessons change the roadmap

The references reinforce four user moments rather than a broad feature checklist:

| User moment | Product outcome | Current evidence | Next gap |
|---|---|---|---|
| Migrate | Turn a fuzzy idea or existing Markdown research project into an explicit project spine without taking ownership away from the user | Versioned project contract, deterministic no-clobber `scriptorium init`, synthetic demo, and explicit metadata-only `scriptorium inventory` routing preview | Adapter-specific, human-reviewed migration manifest and apply path |
| Resume | Give the agent a small, current context capsule instead of replaying all history | Provenance portfolio/current-context MCP tools and the separate content-free control-plane `scriptorium status` | A user-facing project context capsule with goals, active questions, accepted evidence, conflicts, and next actions |
| Close out | Convert a finished agent session into an append-only timeline plus candidate high-value claims | Public `scriptorium pull`, canonical pending-fill guidance, `Approvals.md`, focused Skill validation, and cross-repository E2E | Obtain clean GitHub CI evidence and repeat the flow with external users and real projects |
| Deliver | Produce a paper outline, stage report, or slides from reviewed project state | Steward handoff contracts and optional Lectern path | Shared artifact manifest and claim/citation/integrity gates before delivery |

Near-term product work should therefore prioritize the project context-capsule/resume
experience, adapter-specific reviewed migration execution, a public Skill evaluation
harness, artifact manifests, and delivery
integrity checks. A general
`ResearchQuestion -> Hypothesis -> Claim -> Evidence -> Gap -> Action` graph should be
validated first as skill output over existing files; it should not trigger a large set
of new schemas before the user loop proves useful. General knowledge graphs, default
vector RAG, and large autonomous multi-agent writing pipelines remain later options.

## Attribution and reuse policy

- This review borrows abstract mechanisms and links to their original projects; no
  upstream source file, prompt, template, schema, image, or benchmark result is included.
- Any future material reuse must be reviewed at a pinned upstream revision, preserve
  the applicable copyright/license notices, and be recorded in a third-party notice.
- Academic Research Skills is published under CC BY-NC 4.0. Because Scriptorium may
  support future commercial work, its expressive materials must not be copied without
  separate permission.
- A repository without an explicit license is treated as all-rights-reserved for reuse:
  it may be linked and discussed, but its implementation or documentation is not copied.
- Product names and trademarks belong to their respective owners. Acknowledgement does
  not imply endorsement.
