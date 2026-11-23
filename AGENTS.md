# AGENTS.md

This repo holds one Inspect AI evaluation, `cot-redhanded`, as the Python package `cot_redhanded` under `src/`. These notes are for coding agents working in it.

## Commands

This project uses `uv`. Do not use `pip`, bare `python` or bare `pytest`.

- `uv sync` installs the project and its dev dependencies.
- `uv run inspect eval cot_redhanded/cot_redhanded --model <provider/model>` runs the eval. `--limit N` runs N samples.
- `make test` runs the tests. Slow tests need `uv run pytest --runslow`.
- `make check` runs every check CI runs.
- `make readmes` regenerates the README sections that come from `eval.yaml` and the task signatures.

## Layout

- `src/cot_redhanded/cot_redhanded.py`: the two tasks, `cot_redhanded` and `cot_redhanded_monitor`, and the version read from `eval.yaml`.
- `src/cot_redhanded/dataset.py`: loads BigCodeBench and builds honest and attack samples (`record_to_sample`).
- `src/cot_redhanded/prompts.py`: every prompt, as constants. The texts are the reference implementation's; a test holds their digest.
- `src/cot_redhanded/side_tasks.py`, `verifiers.py`: the eleven side tasks and the sandbox check for each.
- `src/cot_redhanded/extraction.py`: pulls the code and the reasoning out of a completion.
- `src/cot_redhanded/monitor.py`, `scorers.py`: the monitor calls and the scorers that use them.
- `src/cot_redhanded/solvers.py`: the retry loop used when `max_attempts` is above 1.
- `src/cot_redhanded/metrics.py`: `control_metrics` (catch rate at a fixed false positive rate, AUC) and its arithmetic. `analysis.py` is a command-line wrapper over it for saved logs.
- `src/cot_redhanded/transcripts.py`: reads saved generation logs for `cot_redhanded_monitor`.
- `src/cot_redhanded/data/`, `sandbox/`: data files, and the Docker sandbox the code runs in.
- `src/cot_redhanded/run_configs/original.yaml`: the paper's design as an Inspect run config, with the source of each value.
- `src/cot_redhanded/eval.yaml`: title, description, contributors, version and tasks. Edit it and run `make readmes`.
- `src/cot_redhanded/README.md`: the eval's documentation, including the generated sections.
- `tests/cot_redhanded/`: the tests.
- `agent_artefacts/trajectory_analysis/inspect_scout/`: Scout scanners for the transcript audit.
- `agent_artefacts/cot_redhanded/parity/`: the scripts that compared this implementation with the reference one, and wrote `tests/cot_redhanded/fixtures/`.

## Checks

`make check` runs ruff (lint and format), basedpyright in standard mode, inspect-evals-lint, `inspect-evals-readmes generate --check` and the pre-commit stack (zizmor, actionlint, mdformat). CI runs the same through python-project-template's shared workflow plus `eval-checks.yml`.

Tests must be deterministic and must not reach the network unless marked: use `mockllm/model` for model output, `unittest.mock` for external services and `tmp_path` for files. Mark tests that download datasets with `@pytest.mark.dataset_download`, that need Docker with `@pytest.mark.docker`, and that take long with `@pytest.mark.slow`. Add `@pytest.mark.huggingface` to a test that calls the Hugging Face Hub, so a test that downloads a dataset from the Hub carries both markers. Tests marked `huggingface` run when `HF_TOKEN` is unset, which includes CI, and the test summary then says how many ran without authentication.

## Scout View and the space in this repo's path

This project lives under `.../Generality Labs/extension programme/...`. **Scout View cannot resolve a path containing a space**, so scans or transcripts addressed through the project path are invisible to it — the viewer shows an empty list, with no error, while the scan itself succeeded and the parquet files are on disk.

The tell-tale is that everything *except* the viewer works: `scout scan list <dir>` finds the scans and the HTTP API returns them. That means path resolution, not a scan failure.

So both locations live outside the project, and `scout.yaml` points at them absolutely:

- `~/scout_scans/cot-redhanded` — scan results (a real directory)
- `~/cot-redhanded-eval` — a symlink to this repo, so `logs/` resolves without spaces

Recreate them if the symlink dangles or the directory is lost:

```bash
mkdir -p ~/scout_scans && ln -s "$PWD" ~/cot-redhanded-eval
```

Three further gotchas:

- `scout view` opens on Project Settings or Transcripts. **Click SCANS in the left icon rail.**
- Scout View's **Project Settings page edits `scout.yaml`**. Opening and saving it rewrites the Locations with whatever the form holds; it has already replaced the scans path with `./scans` once. Comments survive, values do not.
- The Inspect AI VS Code extension hides its entire Scout activity bar behind `inspect_ai.inspect_scout.installed`, resolved against the **selected Python interpreter**. `inspect_scout` is only in `.venv`, so if VS Code picks another interpreter the Scout UI and every `Scout View:` command silently vanish. `.vscode/settings.json` pins the interpreter via `python.defaultInterpreterPath`; select it once and reload the window, since VS Code caches the current selection. That file is plain JSON, not JSONC, because the repo's `check-json` pre-commit hook rejects comments.

## Guides

- [BEST_PRACTICES.md](https://github.com/UKGovernmentBEIS/inspect_evals/blob/main/BEST_PRACTICES.md)
- [EVALUATION_CHECKLIST.md](https://github.com/UKGovernmentBEIS/inspect_evals/blob/main/EVALUATION_CHECKLIST.md)
- [TASK_VERSIONING.md](https://github.com/UKGovernmentBEIS/inspect_evals/blob/main/TASK_VERSIONING.md): bump `version` in `eval.yaml` when a change can move scores.
- [CONTRIBUTING.md](https://github.com/UKGovernmentBEIS/inspect_evals/blob/main/CONTRIBUTING.md) in inspect_evals, for the `eval.yaml` reference and the register.
- [inspect-evals-lint rules](https://github.com/Generality-Labs/inspect-evals-lint/tree/main/docs/rules), one page per rule code.

## Skills

The `inspect-evals` plugin from [Generality-Labs/agent-skills](https://github.com/Generality-Labs/agent-skills) provides `create-eval`, `investigate-dataset`, `ensure-test-coverage`, `read-eval-logs`, `check-trajectories-workflow`, `eval-validity-review` and `eval-report-workflow`. Claude Code loads it from `.claude/settings.json`; see README.md for Codex and other agents.
