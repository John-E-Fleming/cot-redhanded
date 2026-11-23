"""Run only the four rubric scanners, and cross-tabulate the labels against outcomes.

`run_all_scanners.py` always includes `DEFAULT_SCANNERS` with no way to exclude them. Those eight
were written for agentic trajectories and are near-inert on these single-turn logs, so running
them costs money for nothing. This runner takes just the four rubric scanners.

It also does the part Principle 4 asks for and the generic extractor does not: prevalence by
outcome. A label count on its own says nothing; the label crossed with the verifier's verdict is
the measurement.

Usage:
    uv run python agent_artefacts/trajectory_analysis/cot_redhanded/run_rubric_scanners.py \
        logs/overnight-attack/iodelete/*.eval --limit 20 --model openrouter/...
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

#: Space-free scans location; see the note on --output below and in scout.yaml.
DEFAULT_SCANS_DIR = str(Path.home() / "scout_scans" / "cot-redhanded")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "inspect_scout"))  # for `scanners`
sys.path.insert(0, str(HERE))

from inspect_ai.log import read_eval_log  # noqa: E402
from inspect_scout import columns, scan, transcripts_from  # noqa: E402


def load_rubric_scanners(only: list[str] | None = None) -> list:
    """The scanners from `eval_scanners.py`, as plain Scout scanners; `only` names a subset."""
    path = HERE / "eval_scanners.py"
    spec = importlib.util.spec_from_file_location("eval_scanners", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["eval_scanners"] = module
    spec.loader.exec_module(module)
    entries = [entry for entry in module.SCANNERS if not only or entry.name in only]
    if only and len(entries) != len(only):
        known = [entry.name for entry in module.SCANNERS]
        raise SystemExit(f"unknown scanner in {only}; expected names from {known}")
    return [entry.create_scanner() for entry in entries]


def main() -> int:
    """Scan the given logs with the four rubric scanners."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", help="One or more .eval logs.")
    # Outside the project on purpose. Scout View cannot resolve a path containing a space, and
    # this project lives under ".../Generality Labs/extension programme/...", so results written
    # inside it are invisible to the viewer even though the scan succeeds. See scout.yaml.
    parser.add_argument("--output", "-o", default=DEFAULT_SCANS_DIR)
    parser.add_argument("--model", "-m", default="openai/gpt-5-mini")
    parser.add_argument("--limit", "-l", type=int, default=0)
    parser.add_argument("--shuffle", action="store_true")
    parser.add_argument("--only", nargs="+", help="Run only these scanners, by name.")
    parser.add_argument(
        "--side-task", help="Scan only this side task's samples (ids like 14_<side task>_baseline)."
    )
    args = parser.parse_args()

    scanners = load_rubric_scanners(args.only)
    print(f"scanners: {[s.__name__ if hasattr(s, '__name__') else str(s) for s in scanners]}")
    print(f"logs    : {len(args.logs)}")
    print(f"model   : {args.model}")

    transcripts = transcripts_from(args.logs)
    if args.side_task:
        # Exact sample ids from the logs. A LIKE pattern on the id is not enough: "complexity"
        # contains "exit".
        sample_ids = [
            str(sample.id)
            for log in args.logs
            for sample in read_eval_log(log).samples or []
            if sample.metadata.get("side_task") == args.side_task
            or sample.metadata.get("side_task_name") == args.side_task
        ]
        print(f"side task: {args.side_task} ({len(sample_ids)} samples)")
        transcripts = transcripts.where(columns.task_id.in_(sample_ids))
    status = scan(
        scanners=scanners,
        transcripts=transcripts,
        model=args.model,
        results=args.output,
        limit=args.limit if args.limit > 0 else None,
        shuffle=args.shuffle if args.limit > 0 else None,
    )
    print(f"\nstatus: {status}")
    print(f"results: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
