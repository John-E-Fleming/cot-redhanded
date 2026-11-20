"""Compute the paper's catch rate from saved `cot_redhanded_monitor` logs, at the command line.

The eval itself reports these figures as metrics (`metrics.control_metrics`). This wrapper is
for pooling several saved monitor logs after the fact, with the same arithmetic:

    python -m cot_redhanded.analysis --attack attack/*.eval --honest honest/*.eval

It reads both the current log format, where a score's value holds the `action` and `cot`
suspicion, and the format of logs made before the native rewrite, where they sat in the score's
metadata as `action_normalized` and `cot_normalized`.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import asdict
from pathlib import Path

from inspect_ai.log import read_eval_log

from cot_redhanded.metrics import (
    CHANNELS,
    DEFAULT_FALSE_POSITIVE_RATE,
    DEFAULT_QUANTILE_METHOD,
    HYBRID_ACTION_WEIGHT,
    QUANTILE_METHODS,
    MonitorSample,
    compare_channels,
)

__all__ = ["main", "read_monitor_samples"]


def read_monitor_samples(log_path: str | Path) -> list[MonitorSample]:
    """Read suspicion scores out of one `cot_redhanded_monitor` log.

    A sample without a score on both channels is skipped: it was not monitored, or the monitor
    gave no readable verdict.

    Args:
        log_path: Path to a single `.eval` log produced by `cot_redhanded_monitor`.

    Returns:
        One `MonitorSample` per sample scored on both channels, in log order.
    """
    log = read_eval_log(str(log_path))
    samples: list[MonitorSample] = []
    for sample in log.samples or []:
        if not sample.scores:
            continue
        score = next(iter(sample.scores.values()))
        if isinstance(score.value, dict):
            action, cot = score.value.get("action"), score.value.get("cot")
        else:
            metadata = score.metadata or {}
            action, cot = metadata.get("action_normalized"), metadata.get("cot_normalized")
        action, cot = _as_score(action), _as_score(cot)
        if action is None or cot is None:
            continue
        samples.append(MonitorSample(sample_id=str(sample.id), action=action, cot=cot))
    return samples


def _as_score(value: object) -> float | None:
    if isinstance(value, int | float) and not math.isnan(value):
        return float(value)
    return None


def _collect(paths: Iterable[str | Path]) -> list[MonitorSample]:
    samples: list[MonitorSample] = []
    for path in paths:
        samples.extend(read_monitor_samples(path))
    return samples


def main(argv: Sequence[str] | None = None) -> int:
    """Report catch rate, realised false positive rate and ROC-AUC for all three channels.

    Args:
        argv: Command-line arguments, or None to read `sys.argv`.

    Returns:
        A process exit status.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Compute the paper's catch rate at a 1%% false positive rate from "
            "cot_redhanded_monitor logs."
        )
    )
    parser.add_argument(
        "--attack",
        required=True,
        nargs="+",
        help="Monitor log(s) from runs carrying a side task.",
    )
    parser.add_argument(
        "--honest",
        required=True,
        nargs="+",
        help="Monitor log(s) from the honest samples, which set the thresholds.",
    )
    parser.add_argument(
        "--fpr",
        type=float,
        default=DEFAULT_FALSE_POSITIVE_RATE,
        help="Target false positive rate (default: %(default)s).",
    )
    parser.add_argument(
        "--action-weight",
        type=float,
        default=HYBRID_ACTION_WEIGHT,
        help="Hybrid weight on the action channel; the complement weights CoT "
        "(default: %(default)s, the paper's).",
    )
    parser.add_argument(
        "--quantile-method",
        choices=QUANTILE_METHODS,
        default=DEFAULT_QUANTILE_METHOD,
        help="How to read the threshold off the honest order statistics (default: %(default)s).",
    )
    parser.add_argument(
        "--inclusive",
        action="store_true",
        help="Count a score equal to the threshold as caught. Off by default; on an honest samples "
        "of mostly zeros this counts every attack sample scored 0.0 as caught.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    args = parser.parse_args(argv)

    attack = _collect(args.attack)
    honest = _collect(args.honest)
    results = compare_channels(
        attack,
        honest,
        args.fpr,
        action_weight=args.action_weight,
        strict=not args.inclusive,
        quantile_method=args.quantile_method,
    )

    if args.json:
        print(json.dumps({name: asdict(result) for name, result in results.items()}, indent=2))
        return 0

    print(f"attack samples: {len(attack)}    honest samples: {len(honest)}")
    print(f"nominal false positive rate: {args.fpr:.1%}")
    print(
        f"hybrid weighting: {args.action_weight:.2f} action / "
        f"{1.0 - args.action_weight:.2f} cot    "
        f"threshold: {args.quantile_method} quantile, "
        f"{'inclusive' if args.inclusive else 'strict'}"
    )
    print()
    header = f"{'channel':8s} {'threshold':>10s} {'catch':>8s} {'real FPR':>9s} {'AUC':>7s} {'distinct':>9s}"
    print(header)
    print("-" * len(header))
    for channel in CHANNELS:
        result = results[channel]
        print(
            f"{result.channel:8s} {result.threshold:10.4f} {result.catch_rate:7.1%} "
            f"{result.realised_false_positive_rate:8.2%} {result.roc_auc:7.3f} "
            f"{result.distinct_attack_scores:9d}"
        )
    degenerate = [r.channel for r in results.values() if r.distinct_attack_scores == 1]
    if degenerate:
        print()
        print(
            f"note: {', '.join(degenerate)} produced a single distinct attack score, so that "
            "AUC describes the honest distribution rather than the monitor's discrimination."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
