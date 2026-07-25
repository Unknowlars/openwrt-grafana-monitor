# Current status

## Current goal

Improve repository readiness for coding agents while preserving project behavior,
paths, workflows, and deployment assumptions.

## Confirmed findings

- Root `AGENTS.md` was absent.
- Existing `CLAUDE.md` duplicated shared guidance and was untracked.
- README dashboard count/tree lags current generators and provisioning outputs.
- `.gitignore` ignored most validation files under `tests/`.
- `.env` is tracked and contains non-placeholder private values. Do not copy
  values from it into docs, prompts, logs, or agent files.
- No structural file moves were justified by the audit.

## Completed

- Added `.agent/repository-agent-audit.md`.
- Added `.agent/repository-agent-plan.md`.
- Added shared root `AGENTS.md`.
- Simplified `CLAUDE.md` to import shared instructions.
- Added bounded `.agent/STATUS.md` and `.agent/CHANGELOG.md`.
- Added `docs/repository-map.md`.
- Updated README to link to the repository map and current dashboard source/output
  set.
- Updated `.gitignore` so validation files under `tests/` are visible while
  Python caches remain ignored.
- Validation passed with Python unit discovery, shell syntax, Compose config,
  whitespace diff check, and `sh tests/run_all.sh`.

## Next action

Human review: decide whether to stage the newly visible validation files under
`tests/`, and handle tracked `.env` secret remediation separately.

## Blockers

- Secret remediation for tracked `.env` needs explicit human approval and likely
  credential rotation.

## Relevant files

- `AGENTS.md`
- `CLAUDE.md`
- `.agent/repository-agent-audit.md`
- `.agent/repository-agent-plan.md`
- `.agent/STATUS.md`
- `.agent/CHANGELOG.md`
- `docs/repository-map.md`
- `.gitignore`
- `README.md`
