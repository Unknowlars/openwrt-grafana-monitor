# Repository agent improvement plan

Date: 2026-07-25

## Safe instruction improvements

### 1. Create root `AGENTS.md`

- Problem being solved: Shared repository policy exists only in untracked `CLAUDE.md`.
- Files affected: `AGENTS.md`.
- Expected benefit: Claude Code, Codex, OpenCode, and similar tools can find the same durable constraints without rediscovering source/output rules, validation commands, and live-system safety.
- Risk: Overlong or stale instructions could increase prompt cost.
- Verification: Review file length and content; ensure it excludes current Git status, secrets, generated JSON, and historical logs.
- Rollback: Remove `AGENTS.md`.

### 2. Simplify `CLAUDE.md`

- Problem being solved: Claude-only copy duplicates shared guidance.
- Files affected: `CLAUDE.md`.
- Expected benefit: One shared source of instruction truth; lower chance of drift.
- Risk: Some Claude environments may not support `@AGENTS.md`; add a short fallback note.
- Verification: Confirm `CLAUDE.md` contains only import and Claude-specific guidance.
- Rollback: Restore previous `CLAUDE.md` content from Git/worktree diff.

### 3. Add explicit efficient-navigation and exclusion rules

- Problem being solved: Large generated/reference files and long historical docs invite unnecessary context loading.
- Files affected: `AGENTS.md`, `docs/repository-map.md`.
- Expected benefit: Agents start from source files and line ranges instead of generated JSON or large plans.
- Risk: Agents may skip relevant generated outputs; mitigate by saying generated JSON can be inspected when validating output pairs or imports.
- Verification: Confirm generated folders are documented as normally excluded but not globally forbidden.
- Rollback: Remove or narrow the exclusion section.

### 4. Add `.agent` memory rules

- Problem being solved: No bounded durable handoff files exist, while ignored `.agents/` can be mistaken for repository state.
- Files affected: `AGENTS.md`, `.agent/STATUS.md`, `.agent/CHANGELOG.md`.
- Expected benefit: Short current-state handoffs without unbounded logs or private data.
- Risk: Agents might overuse these files; include size and update rules.
- Verification: Confirm `.agent/STATUS.md` is concise and `.agent/CHANGELOG.md` has append-only template.
- Rollback: Remove `.agent/STATUS.md` and `.agent/CHANGELOG.md`.

## Safe non-structural repository improvements

### 1. Add concise `docs/repository-map.md`

- Problem being solved: README repo structure is stale and operator-focused.
- Files affected: `docs/repository-map.md`, `README.md`.
- Expected benefit: Agents can choose correct source, generated output, docs, and validation entry points without scanning the tree.
- Risk: Map can become stale as generators change.
- Verification: Compare map against current tracked source/output files.
- Rollback: Remove map and README link.

### 2. Update `.gitignore` for validation harness visibility

- Problem being solved: Current ignore rules hide most of `tests/`, including `tests/run_all.sh` named by instructions.
- Files affected: `.gitignore`.
- Expected benefit: Future Git status can show validation scripts/fixtures that should be reviewed for tracking, making advertised validation reproducible.
- Risk: More ignored-local files under `tests/` may appear in status.
- Verification: Run `git status --ignored --short tests` and confirm `__pycache__` remains ignored while validation files are no longer ignored.
- Rollback: Restore previous tests ignore block.

### 3. Add README link to repository map

- Problem being solved: Agents may rely on stale README tree.
- Files affected: `README.md`.
- Expected benefit: Directs agents and contributors to the maintained map.
- Risk: Minimal.
- Verification: Confirm the link target exists.
- Rollback: Remove the link.

### 4. Document tracked `.env` risk without editing secrets

- Problem being solved: `.env` is tracked and contains non-placeholder private values.
- Files affected: `AGENTS.md`, `.agent/STATUS.md`, `.agent/CHANGELOG.md`.
- Expected benefit: Agents avoid reading/copying `.env`; maintainers have an explicit redacted follow-up.
- Risk: Does not remediate already-exposed secrets.
- Verification: Search new files for sensitive values and ensure no secret content is copied.
- Rollback: Remove the documentation note.

## Structural changes

No structural changes are planned.

The audit rejected moves of generated dashboards, `grafana dashboards/`, large historical docs, ignored local skill/reference material, and component-level instruction files. The observed problems are better solved by shared instructions, a concise map, and ignore-rule corrections.

