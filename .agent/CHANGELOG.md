# Agent changelog

## 2026-07-25

- Changed: Added `docs/CODE-REVIEW-REMEDIATION-PLAN.md`, a prescriptive fix plan
  and task list (`R1`-`R13`, `M1`-`M2`) from a second full-repo review, and
  linked it from `docs/repository-map.md`.
- Reason: The 2026-07-23 `CODE-REVIEW-FINDINGS.md` is deliberately report-only.
  This pass found 13 further issues plus 2 metric gaps not covered there, and an
  implementing agent needs prescribed fixes, tests, and verification commands.
- Validation: Documentation only, no behavior change. `git diff --check`,
  `python3 -m unittest discover -s tests -p 'test_*.py'`,
  `sh -n openwrt/setup.sh openwrt/scripts/*.sh`, and
  `docker compose config --quiet` passed. Four claims in the plan were
  reproduced locally; commands and output are in its Appendix A.
- Remaining risk: All 15 tasks are unimplemented. `R1`'s original claim about
  the Alloy image `CMD` was wrong and is corrected in the plan — do not re-derive
  it from the first review summary.

- Changed: Added shared root agent instructions, bounded `.agent` handoff files,
  a repository map, an audit/plan for agent-readiness work, and a current README
  dashboard map.
- Reason: Reduce repeated discovery, centralize durable constraints, and make
  generated/source boundaries easier for coding agents to find.
- Validation: `python3 -m unittest discover -s tests -p 'test_*.py'`,
  `sh -n openwrt/setup.sh openwrt/scripts/*.sh`, `docker compose config --quiet`,
  `git diff --check`, and `sh tests/run_all.sh` passed.
- Remaining risk: Tracked `.env` contains private values and requires separate
  human-approved remediation.
