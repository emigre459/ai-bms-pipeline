# AI Agent Instructions — gridium-bms-ai-pipeline

> This file is the single source of truth for AI assistants working in this repository.
> `CLAUDE.md` is a symlink to this file.
> Cursor rules live in `.cursor/rules/` and are NOT duplicated here — treat them as authoritative for their declared scopes.

---

## Cursor Rules (read these; do not override them)

| File | Scope | Summary |
|------|-------|---------|
| `.cursor/rules/uv-python.mdc` | always | Use `uv` for all Python execution and dependency management — never `pip` or bare `python` |
| `.cursor/rules/python-best-practices.mdc` | `**/*.py` | Code style, type hints, testing (TDD), API design, error handling, documentation conventions |

When the cursor rules and this file conflict, prefer the cursor rule for Python/tooling specifics and this file for project-specific architecture decisions.

---

## Project Overview

A two-stage AI pipeline that ingests BMS (Building Management System) screenshots and produces energy efficiency analyses.

**Stage 1 — Image → JSON:** BMS screenshots are classified (LLM boolean call), then extracted into structured `bms-snapshot.schema.yaml` JSON. A data-quality score gates low-signal extractions before they reach Stage 2.

**Stage 2 — JSON → Analysis:** A deterministic rule engine flags measurable patterns (HWS temp vs. OAT, fan speed imbalance, economiser state, simultaneous heating/cooling, etc.). Those findings ground an LLM synthesis call that produces `analysis-output.schema.yaml` output. **All arithmetic (cost/carbon) is computed deterministically in code, never by the LLM.**

---

## Repository Layout

```
src/ai_bms_pipeline/    # Main package (src-layout)
  config.py             # Constants, MAX_CONCURRENT_LLM_TASKS, schema validation helpers
  image_ingest.py       # Stage 1: classifier + YAML-schema-driven extraction
  analysis.py           # Stage 2: deterministic rule engine + LLM synthesis
  utils.py              # Shared utilities
  logs.py               # HTML report generation
scripts/
  pipeline_example.py   # End-to-end demo runner
  images_to_structured_data.py
  analyze_buildings.py
  pdf_to_markdown.py
tests/                  # pytest; runs in parallel via pytest-xdist
conf/                   # YAML schema files (bms-snapshot, analysis-output)
data/images/            # Input images (not committed); building-n/ subdirs = one building
logs/                   # HTML report output (generated at runtime)
references/             # Design notes and diagrams
```

---

## Environment Setup

```bash
uv sync                    # install/sync dependencies from lockfile
cp .env.example .env       # then fill in ANTHROPIC_API_KEY + DEFAULT_ANTHROPIC_MODEL
```

Required `.env` keys:
- `ANTHROPIC_API_KEY`
- `DEFAULT_ANTHROPIC_MODEL` (e.g. `claude-sonnet-4-6`)

Python version: **3.12.x** (pinned in `pyproject.toml`).

---

## Running the Pipeline

```bash
uv run scripts/pipeline_example.py   # full end-to-end demo (~3-5 min)
```

---

## Testing

```bash
uv run pytest                        # all tests, parallelised automatically (-n auto)
uv run pytest -m "not integration"   # skip live API calls
uv run pytest tests/test_analysis.py # single file
```

- Tests are in `tests/`; keep them independent so `-n auto` parallelism works.
- Mark any test that calls a live external API with `@pytest.mark.integration`.
- Follow **red-green TDD**: write failing tests first, then make them pass.
- Use `pytest-mock` for mocking; do not mock the LLM clients for unit tests that can be designed to avoid needing them.

---

## Key Architectural Constraints

1. **LLM produces data, code computes arithmetic.** Never ask the LLM to calculate costs or carbon figures — all numeric aggregation uses `DEFAULT_FACTORS` in `analysis.py`.
2. **YAML schemas are authoritative for output shape.** `conf/*.schema.yaml` drives both the LLM's `output_config` and the `validate_against_schema()` helper in `config.py`. If you add a field, add it to the schema first.
3. **Concurrency cap.** `MAX_CONCURRENT_LLM_TASKS = 50` in `config.py`. Do not bypass this when adding new async LLM calls.
4. **Compact snapshot format.** Each BMS snapshot is serialised as a JSON string inside the array (not a nested object) to stay within Anthropic's grammar size limits — keep this when touching the extraction schema.

---

## Skills

Skills follow the [Agent Skills](https://agentskills.io) open standard and live in `.claude/skills/`. They work in Claude Code and any other compatible agent tool.

| Skill | Invoke | Description |
|-------|--------|-------------|
| `run-tests` | `/run-tests` | Runs `make tests` and reports results. Manual-only — won't auto-trigger. |
| `pr-check` | `/pr-check` | Runs `make pr_check` (Black + tests). Use before opening a PR. Manual-only. |

Bundled Claude Code skills also available: `/simplify`, `/commit`, `/claude-api`.

## Hooks

`.claude/settings.json` registers a `Stop` hook that runs `.claude/hooks/run-tests-on-stop.sh` at the end of every Claude turn. The script is a no-op unless Python files were modified or created; when they were, it runs `make tests`. If tests fail, it exits with code 2, which blocks Claude from finishing so it can address the failures.

---

## Do Not

- Run `pip install` — use `uv add` / `uv sync`
- Run `python script.py` — use `uv run python script.py`
- Let the LLM compute cost/carbon totals
- Add fields to LLM output without updating the corresponding `conf/*.schema.yaml`
- Write tests that depend on test execution order (parallelism breaks them)
