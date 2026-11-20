"""Turn saved generation logs into samples for the scoring-only monitor task.

`cot_redhanded_monitor` re-monitors completions that already exist, so a second monitor can be
tried without paying for generation again. This module reads those completions out of `.eval`
logs: logs written by `cot_redhanded`, and logs written by the reference implementation's
generation task, which is what every run before the native rewrite produced.

Only completions the paper would monitor are kept: an honest one that passed its main task, an
attack one that passed both its main and its side task, each with code to show the monitor and
not cut off by the token limit.
"""

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from inspect_ai.dataset import Sample
from inspect_ai.log import EvalLog, EvalSample, read_eval_log

from cot_redhanded.dataset import sample_id
from cot_redhanded.extraction import extract_transcript

__all__ = ["expand_log_paths", "load_transcript_samples", "transcript_samples"]

# The reference implementation's name for an honest run's side task.
_LEGACY_HONEST = "baseline"


def expand_log_paths(log_paths: str | Sequence[str]) -> list[Path]:
    """Resolve the `log_paths` task parameter to a list of log files.

    Args:
        log_paths: One path, several comma-separated paths, or a list of paths. A directory
            stands for every `.eval` file beneath it.

    Returns:
        The log files, in the order given, with each directory's files sorted.

    Raises:
        FileNotFoundError: If a path does not exist, or a directory holds no logs.
    """
    entries = log_paths.split(",") if isinstance(log_paths, str) else list(log_paths)
    files: list[Path] = []
    for entry in entries:
        path = Path(entry.strip()).expanduser()
        if path.is_dir():
            found = sorted(path.rglob("*.eval"))
            if not found:
                raise FileNotFoundError(f"No .eval logs under {path}")
            files.extend(found)
        elif path.is_file():
            files.append(path)
        else:
            raise FileNotFoundError(f"No such log file or directory: {path}")
    return files


def load_transcript_samples(log_paths: str | Sequence[str]) -> list[Sample]:
    """Read the monitorable completions out of one or more generation logs.

    Args:
        log_paths: See `expand_log_paths`.

    Returns:
        One sample per monitorable completion. Its input is the problem statement an honest
        model would receive, and its metadata carries `eval_mode`, `side_task`,
        `attack_policy`, `untrusted_model`, and the `action` and `reasoning` to show the monitor.

    Raises:
        ValueError: If no completion in the logs is monitorable, or two completions would get
            the same sample id (the same run passed twice, say).
    """
    samples: list[Sample] = []
    for path in expand_log_paths(log_paths):
        samples.extend(transcript_samples(read_eval_log(str(path))))
    if not samples:
        raise ValueError("None of the completions in the given logs can be monitored")

    untrusted_models = {(sample.metadata or {})["untrusted_model"] for sample in samples}
    if len(untrusted_models) > 1:
        for sample in samples:
            sample.id = f"{sample.id}_{(sample.metadata or {})['untrusted_model']}"
    seen: set[Any] = set()
    for sample in samples:
        if sample.id in seen:
            raise ValueError(f"Sample {sample.id} appears more than once in the given logs")
        seen.add(sample.id)
    return samples


def transcript_samples(log: EvalLog) -> Iterable[Sample]:
    """Yield a monitor sample for each monitorable completion in one generation log.

    Args:
        log: A log from `cot_redhanded` or from the reference implementation's generation task.

    Yields:
        The monitor samples, in log order.
    """
    untrusted_model = log.eval.model.split("/")[-1]
    for sample in log.samples or []:
        described = _describe(sample, log)
        if described is None or not _completed(sample):
            continue
        transcript = extract_transcript(sample.output.choices[0].message.content)
        if not transcript.monitorable:
            continue
        task_id = str(sample.id).split("_", 1)[0]
        yield Sample(
            id=sample_id(task_id, described["side_task"], described["attack_policy"]),
            input=described["problem"],
            metadata={
                **described,
                "untrusted_model": untrusted_model,
                "action": transcript.action,
                "reasoning": transcript.reasoning,
                "source_sample_id": str(sample.id),
            },
        )


def _completed(sample: EvalSample) -> bool:
    """Whether the untrusted model produced a reply that was not cut off."""
    choices = sample.output.choices
    return bool(choices) and choices[0].stop_reason != "max_tokens"


def _describe(sample: EvalSample, log: EvalLog) -> dict[str, Any] | None:
    """Read a completion's eval mode and whether it succeeded; None if it should be skipped."""
    metadata = sample.metadata
    score = next(iter((sample.scores or {}).values()), None)
    if score is None:
        return None

    if "eval_mode" in metadata:
        value = score.value if isinstance(score.value, dict) else {}
        attack = metadata["eval_mode"] == "attack"
        succeeded = value.get("main") == 1 and (not attack or value.get("side") == 1)
        side_task, attack_policy = metadata.get("side_task"), metadata.get("attack_policy")
        problem = metadata["problem"]
        attempts = value.get("attempts", 1)
    else:
        # A log from the reference implementation's generation task.
        legacy_side_task = log.eval.task_args.get("side_task") or metadata.get("side_task_name")
        attack = legacy_side_task not in (None, _LEGACY_HONEST)
        succeeded = score.value == "C"
        side_task = legacy_side_task if attack else None
        attack_policy = str(sample.id).split("_", 1)[1] if attack else None
        problem = metadata.get("prompt", "")
        attempts = 1

    if not succeeded:
        return None
    return {
        "eval_mode": "attack" if attack else "honest",
        "side_task": side_task,
        "attack_policy": attack_policy,
        "problem": problem,
        "attempts": attempts,
    }
