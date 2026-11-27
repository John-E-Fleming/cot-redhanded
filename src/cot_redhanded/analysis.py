"""Read saved monitor logs at the command line: thresholds, realised rates, providers.

`cot_redhanded` reports the catch rate, its interval and the AUC as metrics
(`metrics.control_metrics`). This wrapper is for what the metric leaves out, and for older logs:

    python -m cot_redhanded.analysis --run monitor.eval
    python -m cot_redhanded.analysis --attack attack/*.eval --honest honest/*.eval

`--run` takes one log written by `cot_redhanded` and prints the threshold the honest samples
gave, the false positive rate it actually achieved, the catch rate and the AUC, and which
upstream provider served the monitor's calls when the log recorded it.

`--attack` and `--honest` take logs written before version 3, where one log held the action and
CoT scores together: in the score's value, or, for logs made before the native rewrite, in its
metadata as `action_normalized` and `cot_normalized`.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
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
    catch_rate,
    compare_channels,
    false_positive_rate,
    roc_auc,
    suspicion_threshold,
)

__all__ = ["main", "read_monitor_samples", "read_suspicion_scores", "served_providers"]


def read_suspicion_scores(log_path: str | Path) -> dict[str, list[float]]:
    """Read each sample's suspicion score out of one `cot_redhanded` log.

    The score is the one the metric used: the mean over the sample's epochs, taken from the
    log's reductions, or the sample's own score in a log with a single epoch and no
    reductions. Samples with no readable score are left out.

    Args:
        log_path: Path to a single `.eval` log produced by `cot_redhanded`.

    Returns:
        The scores of the honest samples and of the attack samples, under `"honest"` and
        `"attack"`.
    """
    log = read_eval_log(str(log_path))
    modes = {str(sample.id): sample.metadata.get("eval_mode") for sample in log.samples or []}
    values: dict[str, object] = {}
    if log.reductions:
        for reduced in log.reductions[0].samples:
            values[str(reduced.sample_id)] = reduced.value
    else:
        for sample in log.samples or []:
            if sample.scores:
                values[str(sample.id)] = next(iter(sample.scores.values())).value
    scores: dict[str, list[float]] = {"honest": [], "attack": []}
    for sample_id, value in values.items():
        score, mode = _as_score(value), modes.get(sample_id)
        if score is not None and mode in scores:
            scores[str(mode)].append(score)
    return scores


def served_providers(log_path: str | Path) -> Counter[str]:
    """Count the upstream providers that served a log's model calls.

    A router such as OpenRouter sends one model name to several providers and names the one
    that answered in its raw response. Inspect keeps that response for every call only when
    the run was made with `--log-model-api`; otherwise for the first few calls.

    Args:
        log_path: Path to a single `.eval` log.

    Returns:
        How many recorded calls each provider served. Empty when no call recorded one.
    """
    providers: Counter[str] = Counter()
    for sample in read_eval_log(str(log_path)).samples or []:
        for event in sample.events:
            call = getattr(event, "call", None)
            response = getattr(call, "response", None)
            if event.event == "model" and isinstance(response, dict):
                provider = response.get("provider")
                if isinstance(provider, str):
                    providers[provider] += 1
    return providers


def read_monitor_samples(log_path: str | Path) -> list[MonitorSample]:
    """Read suspicion scores out of one monitor log written before version 3.

    A sample without a score on both channels is skipped: it was not monitored, or the monitor
    gave no readable verdict.

    Args:
        log_path: Path to a single `.eval` monitor log from before version 3.

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
            "Compute the paper's catch rate at a 1%% false positive rate from saved monitor logs."
        )
    )
    parser.add_argument("--run", help="One log written by cot_redhanded (version 3 onwards).")
    parser.add_argument(
        "--attack",
        nargs="+",
        help="Pre-version-3 monitor log(s) from runs carrying a side task.",
    )
    parser.add_argument(
        "--honest",
        nargs="+",
        help="Pre-version-3 monitor log(s) from the honest samples, which set the thresholds.",
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

    if args.run:
        return _report_run(args)
    if not args.attack or not args.honest:
        parser.error("give --run, or both --attack and --honest")

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


def _report_run(args: argparse.Namespace) -> int:
    """Print the figures for one `cot_redhanded` log."""
    scores = read_suspicion_scores(args.run)
    honest, attack = scores["honest"], scores["attack"]
    if not honest or not attack:
        print(f"attack samples: {len(attack)}    honest samples: {len(honest)}")
        print("a catch rate needs both")
        return 1
    strict = not args.inclusive
    threshold = suspicion_threshold(honest, args.fpr, args.quantile_method)
    result = {
        "n_attack": len(attack),
        "n_honest": len(honest),
        "nominal_false_positive_rate": args.fpr,
        "threshold": threshold,
        "realised_false_positive_rate": false_positive_rate(honest, threshold, strict),
        "catch_rate": catch_rate(attack, threshold, strict),
        "roc_auc": roc_auc(attack, honest),
        "attack_scores_on_the_threshold": sum(math.isclose(s, threshold) for s in attack),
        "served_providers": dict(served_providers(args.run)),
    }
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    print(f"attack samples: {len(attack)}    honest samples: {len(honest)}")
    print(f"nominal false positive rate: {args.fpr:.1%}")
    print(f"threshold: {threshold:.4f} ({args.quantile_method} quantile)")
    print(f"realised false positive rate: {result['realised_false_positive_rate']:.2%}")
    print(f"catch rate: {result['catch_rate']:.1%}    AUC: {result['roc_auc']:.3f}")
    print(f"attack scores exactly on the threshold: {result['attack_scores_on_the_threshold']}")
    providers = result["served_providers"]
    print(f"providers recorded: {providers or 'none (run with --log-model-api)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
