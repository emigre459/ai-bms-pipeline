---
name: pr-check
description: Run the full PR readiness check (Black formatting + tests) via `make pr_check`. Use before opening a pull request or when asked to verify code is ready to merge.
compatibility: Requires uv and make
disable-model-invocation: true
allowed-tools: Bash(make pr_check), Bash(make black)
---

Run the full PR readiness check:

```bash
make pr_check
```

This runs `make black` (auto-formats `tests/`, `src/`, `scripts/`) then `make tests`.

Report:
1. Whether Black made any formatting changes — list changed files if so
2. Test pass/fail summary and total count
3. Any test failures with file path, line number, and failed assertion

If checks fail, fix the issues and re-run before finishing.
