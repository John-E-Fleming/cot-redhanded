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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "inspect_scout"))  # for `scanners`
sys.path.insert(0, str(HERE))

from inspect_scout import scan, transcripts_from  # noqa: E402


def load_rubric_scanners() -> list:
    """The four scanners from `eval_scanners.py`, as plain Scout scanners."""
    path = HERE / "eval_scanners.py"
    spec = importlib.util.spec_from_file_location("eval_scanners", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["eval_scanners"] = module
    spec.loader.exec_module(module)
    return [entry.create_scanner() for entry in module.SCANNERS]


def main() -> int:
    """Scan the given logs with the four rubric scanners."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", help="One or more .eval logs.")
    parser.add_argument(
        "--output", "-o", default="agent_artefacts/trajectory_analysis/cot_redhanded/rubric_results"
    )
    parser.add_argument("--model", "-m", default="openai/gpt-5-mini")
    parser.add_argument("--limit", "-l", type=int, default=0)
    parser.add_argument("--shuffle", action="store_true")
    args = parser.parse_args()

    scanners = load_rubric_scanners()
    print(f"scanners: {[s.__name__ if hasattr(s, '__name__') else str(s) for s in scanners]}")
    print(f"logs    : {len(args.logs)}")
    print(f"model   : {args.model}")

    transcripts = transcripts_from(args.logs)
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
