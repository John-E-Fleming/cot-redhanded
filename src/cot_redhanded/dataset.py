"""Build the honest and attack samples from BigCodeBench.

Every sample is one BigCodeBench problem run in one **eval mode**. An honest sample asks for the
problem alone. An attack sample asks for the problem plus a hidden side task, put to the model
through an attack policy. The problems, the splits, the per-problem side-task test cases and the
main-task to side-task pairings are the reference implementation's.
"""

import json
from collections.abc import Sequence
from functools import cache
from pathlib import Path
from typing import Any, Literal, get_args

from inspect_ai.dataset import MemoryDataset, Sample
from inspect_evals_utils.huggingface import load_dataset

from cot_redhanded.prompts import SIDE_TASK_ANCHOR, attack_instructions
from cot_redhanded.side_tasks import side_task_spec

__all__ = [
    "DATASET_PATH",
    "DATASET_REVISION",
    "DATASET_SPLIT",
    "ID_SETS",
    "EvalMode",
    "load_annotations",
    "load_cot_redhanded_dataset",
    "load_id_set",
    "load_pairings",
    "record_to_sample",
    "sample_id",
]

DATASET_PATH = "bigcode/bigcodebench"
#: The BigCodeBench release, which the dataset publishes as a split.
DATASET_SPLIT = "v0.1.4"
DATASET_REVISION = "b74c0d0bf70d2c0bc459be537895cca163007f1a"

#: The reference implementation's splits over the 1,140 BigCodeBench problems.
#: The name of a split of BigCodeBench.
IdSet = Literal["TRAIN", "VAL", "RED", "TEST"]
ID_SETS: tuple[IdSet, ...] = get_args(IdSet)

EvalMode = Literal["honest", "attack"]

_DATA_DIR = Path(__file__).parent / "data"
_TASK_ID_PREFIX = "BigCodeBench/"
_RUN_TESTS = "\n\nunittest.main()"


@cache
def load_id_set(id_set: str) -> frozenset[str]:
    """Read one split's problem ids.

    Args:
        id_set: One of `ID_SETS`.

    Returns:
        The problem ids in the split, as the numeric strings BigCodeBench uses.
    """
    with open(_DATA_DIR / "task_id_sets.json", encoding="utf-8") as file:
        return frozenset(json.load(file)[f"{id_set}_SET"])


@cache
def load_pairings() -> dict[str, str]:
    """Read which side task each problem is paired with.

    Returns:
        A mapping from problem id to the one side task the reference implementation judged the
        best fit for that problem.
    """
    with open(_DATA_DIR / "bigcodebench_pairings.json", encoding="utf-8") as file:
        return {task_id: pairing["task"] for task_id, pairing in json.load(file).items()}


@cache
def load_annotations(side_task: str) -> dict[str, dict[str, Any]]:
    """Read the per-problem test cases for an annotated side task.

    Args:
        side_task: `complexity` or `edgecase`. Any other side task has no annotations.

    Returns:
        A mapping from problem id to that problem's fields for the side task: `side_test_case`
        for both, plus `benchmark_time` for `complexity` and `idea`, `obviousness_rating` and
        `sabotaged_code` for `edgecase`.
    """
    annotations: dict[str, dict[str, Any]] = {}
    with open(_DATA_DIR / "cot_bigcodebench_side_tasks.jsonl", encoding="utf-8") as file:
        for line in file:
            item = json.loads(line)
            if side_task in item:
                annotations[item["id"]] = item[side_task]
    return annotations


@cache
def _load_argument_types() -> dict[str, dict[str, list[str]]]:
    with open(_DATA_DIR / "defaultval_types.json", encoding="utf-8") as file:
        return json.load(file)


def sample_id(task_id: str, side_task: str | None, attack_policy: str | None) -> str:
    """Name a sample.

    Args:
        task_id: The BigCodeBench problem id, without its `BigCodeBench/` prefix.
        side_task: The side task, or None for an honest sample.
        attack_policy: The attack policy, or None for an honest sample.

    Returns:
        `<task_id>_honest`, or `<task_id>_<side_task>_<attack_policy>`.
    """
    if side_task is None:
        return f"{task_id}_honest"
    return f"{task_id}_{side_task}_{attack_policy}"


def record_to_sample(
    record: dict[str, Any], side_task: str | None = None, attack_policy: str | None = None
) -> Sample:
    """Turn one BigCodeBench record into an honest or an attack sample.

    For an attack sample the attack instructions are inserted into the problem statement, on the
    line before "The function should output with:". The unmodified statement is kept in
    metadata as `problem`, because that is what the monitor is shown.

    Args:
        record: A row of `bigcode/bigcodebench`.
        side_task: The side task for an attack sample, or None for an honest one.
        attack_policy: The attack policy for an attack sample. Required with `side_task`.

    Returns:
        The sample. Its target is the problem's unit tests, as a runnable program.
    """
    task_id = record["task_id"].removeprefix(_TASK_ID_PREFIX)
    problem = record["instruct_prompt"]
    argument_types = _load_argument_types()[task_id]

    metadata: dict[str, Any] = {
        "eval_mode": "honest" if side_task is None else "attack",
        "task_id": task_id,
        "side_task": side_task,
        "attack_policy": attack_policy,
        "attack_instructions": None,
        "problem": problem,
        "reference_solution": record["code_prompt"] + record["canonical_solution"],
        "canonical_solution_length": _canonical_solution_length(record["canonical_solution"]),
        "input_types": list(argument_types["input_types"]),
        "output_types": argument_types["output_types"],
    }
    files: dict[str, str] | None = None

    if side_task is not None:
        if attack_policy is None:
            raise ValueError("An attack sample needs an attack policy as well as a side task")
        spec = side_task_spec(side_task)
        instructions = attack_instructions(side_task, attack_policy)
        metadata["attack_instructions"] = instructions
        problem = problem.replace(SIDE_TASK_ANCHOR, f"{instructions}\n{SIDE_TASK_ANCHOR}")
        if spec.side_test_case is not None:
            metadata["side_test_case"] = spec.side_test_case
        if spec.annotated:
            metadata.update(load_annotations(side_task).get(task_id, {}))
        files = dict(spec.files) or None
        # `complexity` and `edgecase` show the model the test case it must pass or slow down.
        problem = problem.replace("{side_test_case}", metadata.get("side_test_case", ""))

    return Sample(
        id=sample_id(task_id, side_task, attack_policy),
        input=problem,
        target=record["test"].strip() + _RUN_TESTS,
        metadata=metadata,
        files=files,
    )


def _canonical_solution_length(canonical_solution: str) -> int:
    """Count the lines of a reference solution the way the reference implementation does.

    It splits on the first colon, meaning to drop the `def` line. A canonical solution has no
    `def` line, so this drops everything up to the first colon in the body instead and
    under-counts most solutions. Kept because `longlines` is scored against this number.
    """
    return len(canonical_solution.split(":", 1)[-1].splitlines())


def _keep(task_id: str, id_set: str, side_task: str | None, use_pairings: bool) -> bool:
    if task_id not in load_id_set(id_set):
        return False
    if side_task is None:
        return True
    if side_task_spec(side_task).annotated and task_id not in load_annotations(side_task):
        return False
    return not use_pairings or load_pairings().get(task_id) == side_task


def load_cot_redhanded_dataset(
    side_tasks: Sequence[str],
    attack_policies: Sequence[str],
    id_set: str,
    use_pairings: bool = False,
    include_honest: bool = True,
) -> MemoryDataset:
    """Load the honest samples and the attack samples for a run.

    There is one attack sample per (problem, side task, attack policy) that survives the
    filters, and one honest sample per problem in the split. The honest samples cover the whole
    split whatever the side tasks, since they calibrate the monitor's false positive rate.

    Args:
        side_tasks: Side tasks to attack with. Empty for an honest-only run.
        attack_policies: Attack policies to put each side task with.
        id_set: Which split of BigCodeBench to use, one of `ID_SETS`.
        use_pairings: Keep an attack sample only when its problem is paired with its side task.
        include_honest: Whether to include the honest samples.

    Returns:
        The honest samples followed by the attack samples.
    """
    samples: list[Sample] = []
    if include_honest:
        samples.extend(_load(None, None, id_set, use_pairings))
    for side_task in side_tasks:
        for attack_policy in attack_policies:
            samples.extend(_load(side_task, attack_policy, id_set, use_pairings))
    return MemoryDataset(samples, name="cot_redhanded", location=DATASET_PATH)


@cache
def _records() -> list[dict[str, Any]]:
    """Read BigCodeBench once per process; every eval mode and side task shares the rows."""
    rows = load_dataset(DATASET_PATH, split=DATASET_SPLIT, revision=DATASET_REVISION)
    return [dict(row) for row in rows]


def _load(
    side_task: str | None, attack_policy: str | None, id_set: str, use_pairings: bool
) -> list[Sample]:
    return [
        record_to_sample(record, side_task, attack_policy)
        for record in _records()
        if _keep(record["task_id"].removeprefix(_TASK_ID_PREFIX), id_set, side_task, use_pairings)
    ]
