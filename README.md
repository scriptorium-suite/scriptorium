# Scriptorium

> **Public Alpha candidate:** the umbrella repository ships safe initialization for
> a real project, one synthetic vertical slice through `scriptorium demo`, the
> read-only `scriptorium doctor`, a content-free `scriptorium status` control-plane
> summary, a zero-write `scriptorium inventory` preview for explicitly selected
> local sources,
> and explicit project-scoped Codex and Claude Code skill installers. It also ships
> the accepted on-demand `scriptorium pull` entry through Provenance's
> machine-readable public command. Clean-machine release validation is still pending.

Scriptorium is a local-first, agent-native research workflow suite. This repository
is its thin control plane: it coordinates independently useful components through
public commands and versioned files without importing their internals or owning
their research data.

[中文说明](README.zh.md) · [Contract source of truth](https://github.com/scriptorium-suite/scriptorium-spec) · [Steward](https://github.com/scriptorium-suite/steward) · [Provenance](https://github.com/foxsplendid/Provenance) · [Design inspirations](ACKNOWLEDGEMENTS.md)

## What works now

`scriptorium init` previews or creates the minimal structure for a real research
project: the suite config, separate Markdown workspace and Provenance data-root
directories, and one valid `project/1.0` note. Preview is the default and `--run`
is required to write. Existing files are never rewritten. Selecting a host records
that choice in config; it does not install the host adapter, a model, or a hook, and
init does not request network access or read provider credentials. After init,
`host install`, `doctor`, `status`, and `pull` can resolve their workspace/data
selection from the suite config when higher-precedence CLI flags or environment
variables are absent.

`scriptorium demo` creates an isolated Markdown workspace and runs a synthetic
AI4Science literature workflow through the real public interfaces:

1. validate a synthetic `library-kb/1.1` with `scriptorium-spec`;
2. call Steward to scope two papers and assemble a review from a recorded agent draft;
3. call Provenance to ingest the library and Markdown project;
4. build and query the local search index;
5. verify portfolio, project context, and literature search through Provenance MCP;
6. write the human-readable artifacts and `demo-report.json`.

The run needs no API key, Zotero, Obsidian, browser extension, or agent login.
Once the source checkouts are installed, this demo path is designed to operate
without a network action. It does not call a live model: the agent-written draft
is an explicitly labelled synthetic fixture. The report states that network
behavior is policy-constrained rather than observed by an OS-level sandbox.
Passing this demo proves deterministic component integration, not full Public
Alpha readiness or scientific validity.

`scriptorium doctor` separately checks installation and capability readiness. It
uses read-only probes, requests no suite-managed network action or GUI launch,
reports optional capability evidence without printing secret values, and
distinguishes the runnable Demo target from the full Public Alpha target. OS-level
subprocess egress is not observed. It verifies a host adapter only when a detected
host CLI has a registered, canonical skill in the selected workspace. It probes
`prov-sync-pull --capabilities --json` instead of assuming that a source checkout
is runnable, and requires an explicit Provenance data root for Public Alpha.

`scriptorium pull` is a thin wrapper over that public Provenance command. Its default
mode is a write-free preview; `--run` explicitly authorizes the local ingest, capture,
single-worker, and approval-queue pass. It does not call a model, install a hook,
request network access, or approve an unchecked claim. A normal first run may return
`action-required`: the selected agent reviews the sanitized pending scaffold and writes
its fill in-session, then another pull applies the low-risk timeline and stages
high-value claims in `Approvals.md`. A user tick is still required before a later pull
commits those claims.

`scriptorium status` is the daily content-free control-plane summary. It first rebuilds
the Public Alpha readiness result from `doctor`; only when that boundary is ready
does it run a `pull` preview. The result contains allowlisted capability states,
aggregate workflow counts, and fixed review cues only. It does not forward local
paths, project or session identifiers, research content, component stderr, or raw
diagnostic details. It never invokes `--run`: `attention` is a normal exit-0
backlog, while incomplete or blocked readiness returns 1 and an untrustworthy report
or a trusted pull-preview error returns 2. Neither status nor its preview authorizes
suite project/data writes;
readiness still invokes external version and capability probes, whose OS-level side
effects are not observed.

`scriptorium inventory` is the safe intake boundary for an existing body of work.
It scans only roots explicitly supplied as Markdown/PDF sources, AI conversation
exports, or Zotero exports. It reads filesystem metadata and filename suffixes only:
it does not open file content or archives, discover personal folders, write a plan,
call a component, request network access, or invoke a model. Its content-free report
suppresses paths and filenames and returns aggregate candidates plus four review
routes: workspace, literature reference, Provenance import, and Steward. The preview
does not validate file contents, deduplicate files, copy data, or claim that a
migration occurred. On Windows, selected objects are held through metadata-only
bindings for the duration of the preview, so another process cannot rename, delete,
or open them for data write until the command finishes.

## Source quickstart on Windows

Prerequisites: Git and Python 3.11+. Clone the four repositories into one parent
directory so the default source discovery is predictable:

```powershell
mkdir scriptorium-workspace
cd scriptorium-workspace
git clone https://github.com/scriptorium-suite/scriptorium.git
git clone https://github.com/scriptorium-suite/scriptorium-spec.git
git clone https://github.com/scriptorium-suite/steward.git
git clone https://github.com/foxsplendid/Provenance.git Provenance

cd scriptorium
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-deps -e .

# Install the two runtime components into the same isolated environment:
.\.venv\Scripts\python.exe -m pip install --no-deps -e ..\steward
.\.venv\Scripts\python.exe -m pip install --no-deps -e ..\Provenance
```

### Optional: preview existing local sources

Pass only local files or directories that you intentionally selected. Zotero and
conversation exports are optional; live Zotero databases and agent profiles are not
auto-discovered.

```powershell
.\.venv\Scripts\scriptorium.exe inventory `
  --source 'D:\Research\Legacy Notes' `
  --conversation-export 'D:\Exports\chat-history.zip' `
  --zotero-export 'D:\Exports\library.bib'
```

The command is classification-only and always remains in preview mode. Its default
terminal and `--json` outputs contain counts and fixed route labels, not local paths,
filenames, research text, hashes, sizes, or timestamps. An incomplete or unsafe scan
fails closed with exit code `1`; malformed invocation or an internal boundary failure
returns `2` without echoing the sensitive input.

### Ten-minute path for a real project

The following path creates a real Markdown project rather than using the synthetic
demo. Keep the workspace and Provenance data root as separate, non-nested
directories. Use `claude-code` consistently in place of `codex` when that is your
selected host.

```powershell
$Workspace = Join-Path $HOME "Research\ai4science-pilot"
$ProvenanceHome = Join-Path $HOME "Research\scriptorium-data"

# Preview only: no file or directory is created.
.\.venv\Scripts\scriptorium.exe init `
  --workspace $Workspace `
  --provenance-home $ProvenanceHome `
  --project-id ai4science-pilot `
  --title "AI4Science Pilot" `
  --host codex `
  --idea "Test whether an evidence-traceable agent workflow improves research continuity."

# Apply the same reviewed plan.
.\.venv\Scripts\scriptorium.exe init `
  --workspace $Workspace `
  --provenance-home $ProvenanceHome `
  --project-id ai4science-pilot `
  --title "AI4Science Pilot" `
  --host codex `
  --idea "Test whether an evidence-traceable agent workflow improves research continuity." `
  --run

# These commands omit workspace/data flags and use the suite config created by init.
.\.venv\Scripts\scriptorium.exe host install codex
.\.venv\Scripts\scriptorium.exe doctor --target public-alpha
.\.venv\Scripts\scriptorium.exe status
```

Open or restart Codex in `$Workspace`, then send this as the first prompt:

```text
$scriptorium-research Read Projects/ai4science-pilot.md, turn the initial intuition into one falsifiable research question, and propose the smallest evidence-backed next step. Do not write high-value project claims until I approve the exact change.
```

Back in PowerShell, preview the local capture/sync plan and then explicitly run the
reviewed plan:

```powershell
.\.venv\Scripts\scriptorium.exe pull
.\.venv\Scripts\scriptorium.exe pull --run
.\.venv\Scripts\scriptorium.exe status
```

By default init writes the suite selection to
`~/.config/scriptorium/scriptorium/config.toml`; use `--config-dir` or
`SCRIPTORIUM_CONFIG_DIR` to select another configuration-family root. The config
stores only its format version, workspace path, Provenance data-root path, selected
hosts, and default project. When absent, init creates `Projects`, `Inbox`, `_planning`,
the separate data-root directory, and the minimal project note. Host adapter installation,
model access, hooks, network actions, and credentials remain separate and explicit. A
`doctor` exit code of `1` is a completed diagnosis with remediation to review, not
a corrupted initialization.

The project note uses the workspace as its session-resolution root by default, which
matches the command path above. If the agent will run from a different existing code
repository, pass that directory explicitly with `--linked-repo` during both preview
and `--run`.

### Optional synthetic integration demo

The credential-free demo remains useful for checking component integration without
using a real project:

```powershell
.\.venv\Scripts\scriptorium.exe doctor `
  --target demo `
  --spec-root ..\scriptorium-spec `
  --steward-root ..\steward `
  --provenance-root ..\Provenance

.\.venv\Scripts\scriptorium.exe demo `
  --output .\scriptorium-demo `
  --spec-root ..\scriptorium-spec `
  --steward-root ..\steward `
  --provenance-root ..\Provenance

# Choose one supported host; run both commands if both hosts should see the skill:
.\.venv\Scripts\scriptorium.exe host install codex `
  --workspace .\scriptorium-demo\workspace
# .\.venv\Scripts\scriptorium.exe host install claude-code `
#   --workspace .\scriptorium-demo\workspace

# Preview first; this makes no authoritative data write:
.\.venv\Scripts\scriptorium.exe pull `
  --workspace .\scriptorium-demo\workspace `
  --provenance-home .\scriptorium-demo\provenance `
  --provenance-root ..\Provenance

# Run the reviewed local plan:
.\.venv\Scripts\scriptorium.exe pull `
  --workspace .\scriptorium-demo\workspace `
  --provenance-home .\scriptorium-demo\provenance `
  --provenance-root ..\Provenance `
  --run
```

When the repositories are adjacent and component commands are discoverable, the
root flags may be omitted:

```powershell
scriptorium doctor --target demo
scriptorium demo
```

Re-running against an output directory carrying the Scriptorium demo marker is
idempotent. A non-empty directory without that marker is rejected and never
overwritten.

## Agent host adapters

`scriptorium host install` projects one packaged `scriptorium-research` Agent Skill
into the selected existing workspace. Codex receives
`.agents/skills/scriptorium-research/SKILL.md`; Claude Code receives
`.claude/skills/scriptorium-research/SKILL.md`. Both files come from the same
canonical source rather than two prompt branches.

```powershell
# Preview only:
scriptorium host install codex --workspace D:\Research\MyProject --dry-run

# Install for one host; repeat with claude-code when desired:
scriptorium host install codex --workspace D:\Research\MyProject
scriptorium host install claude-code --workspace D:\Research\MyProject
```

The command requires an existing workspace selected by `--workspace`, environment,
or suite config; it never falls back to the current directory. It refuses unmanaged
or modified target content, rejects symlink/junction traversal, and records managed hashes in
`.scriptorium/host-adapters.v1.json` so unchanged installs are idempotent and
unmodified older assets can be updated safely. It does not download anything,
log in, launch a GUI, install a hook, or modify global host settings. Open or restart
the selected host in that workspace and verify its skills list; `doctor` validates
the static files and matching CLI, not live model access or in-session discovery.
Concurrent installs fail closed through a workspace lock; after a process crash,
inspect the workspace before removing an empty `.scriptorium/host-install.lock`.

## On-demand pull

Both paths are explicit and local; use `--json` for the stable machine report:

```powershell
scriptorium pull --workspace D:\Research\Workspace --provenance-home D:\Research\ProvenanceData
scriptorium pull --workspace D:\Research\Workspace --provenance-home D:\Research\ProvenanceData --run
```

The paths must already exist. Scriptorium never falls back to the current directory
for research data. A canonical Codex adapter enables the conservative local-log scan
(registered projects only, recent stable logs, Desktop excluded). A Claude Code skill
does not imply that its optional `SessionEnd` enqueue hook was installed or live-tested;
that capture path remains a separate, explicit user configuration. `--project` narrows
Codex discovery only; the workspace ingest and existing sync queue remain workspace-wide.

If the report includes `project-resolution`, the affected events remain in protected
inflight state. Scriptorium will not create a summary, timeline, or draft with an
unresolved project. Approve or add the correct `project_id` / `linked_repo` mapping in
the Markdown workspace, then rerun pull; the same events resume without being retired.
The canonical research skill inspects unresolved items through the read-only, path-
suppressed `prov-sync-unresolved` entry. When `agent-fill` appears, it reads allowlisted
sanitized scaffolds through `prov-sync-pending` and submits approved candidate fills only
through `prov-sync-fill`; it never constructs a protected path or writes `fill.json`
directly. Fill submission and the later authoritative `--run` require separate approval.

Exit `0` includes a successful preview/run and expected `action-required` backlog, `1`
means a safe block or partial component pass, and `2` means the entry could not form a
trustworthy report. The pull report is deliberately aggregate-only: raw component output,
local paths, session identifiers, and research content are suppressed at the entry boundary.

## Readiness diagnostics

The default target is the complete product boundary:

```powershell
scriptorium doctor `
  --workspace .\scriptorium-demo\workspace `
  --provenance-home .\scriptorium-demo\provenance
scriptorium doctor --json `
  --workspace .\scriptorium-demo\workspace `
  --provenance-home .\scriptorium-demo\provenance
```

The command returns `0` when the selected target has no required failure, `1` for
a completed diagnosis with missing requirements, and `2` only when doctor itself
cannot form a trustworthy report. Missing Zotero, Obsidian, PowerPoint, Lectern,
or the browser extension degrades only its capability. Agent authentication,
browser-extension permissions, GUI launch, workspace writes, and live network
behavior remain explicitly untested. Detecting an application or command is not
reported as a successful live integration or provider check. Public Alpha workspace
evidence requires at least one `Projects/*.md` note with complete `project/1.x`
frontmatter; an arbitrary repository README is not treated as a research workspace.
`entry.pull` passes only when the compatible machine-readable capability probe succeeds.
Codex provides the first executable session-capture path; a Claude-only installation
remains a manual readiness item until its opt-in `SessionEnd` hook is live-verified.

## Suite workflow status

After `init`, the suite config makes the daily command pathless:

```powershell
scriptorium status
scriptorium status --json
```

The command is a content-free aggregation, not a sync authorization. It reports Public
Alpha readiness, optional Literature/Slides/Web-history capability states, a freshness
state based on the current pull preview, aggregate pending counts, and ordered review
cues. `review-pull-plan` opens a normal preview, while `pull-diagnostics` reopens the
same content-free public diagnostic entry after a blocked/error result. Both point to
plain `scriptorium pull`; the user must review a separate preview before explicitly
adding `--run`. Project resolution, agent fill, approval, and workspace review remain
human/agent cues rather than commands that claim to complete those actions.

`ready` and `attention` return 0 so normal human review backlog does not look like
an infrastructure failure. `incomplete` or `blocked` returns 1. `error` returns 2
when a trusted pull preview reports an error or when the entry cannot form a
trustworthy report. The last successful pull time remains `not-reported` until a
stable component contract exists; the command does not invent an age threshold or
parse the legacy human-readable Provenance status output.

## Demo outputs

```text
scriptorium-demo/
├── fixtures/                         # explicitly synthetic inputs
├── workspace/
│   ├── Projects/                     # project/1.0 Markdown
│   ├── Reviews/                      # Steward-assembled review
│   └── Reports/                      # Provenance search + MCP evidence
├── provenance/
│   ├── memory/                       # isolated library/project snapshots
│   └── search-index.db               # isolated local FTS5 index
└── demo-report.json                  # stages, assertions, limits, artifacts
```

All subprocesses run with `PROVENANCE_HOME`, `PROVENANCE_VAULT`, temporary files,
and configuration homes redirected under the demo directory. Child processes
receive a minimal environment allowlist rather than the user's model, Zotero, or
provider credentials. The entry contains no network client; OS-level egress and
filesystem tripwires remain a later release-hardening step and are not implied by
the current report.

## Compatibility baseline

The first golden path intentionally locks exact source versions:

- `scriptorium-spec` 2.2.0 release candidate
- Steward 0.2.0 unreleased
- Provenance 0.17.0

This source-based constraint is temporary and explicit. Tagged releases and a
range-based compatibility policy belong to the later Public Alpha release step.

## Next product increments

1. add a compact project context-capsule/resume entry over Provenance MCP, distinct
   from the content-free control-plane `status`;
2. add uniform side-effect-free version probes and profile compatibility facts;
3. add an explicitly reviewed, adapter-specific migration manifest and apply path;
4. add schema-driven cross-repository E2E for Lectern handoff;
5. verify installation and the golden path on a clean Windows environment;
6. align screenshots, release notes, tags, and compatibility ranges for Public Alpha.

Apache-2.0. No telemetry.
