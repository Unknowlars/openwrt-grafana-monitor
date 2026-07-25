# Agent changelog

## 2026-07-25

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
