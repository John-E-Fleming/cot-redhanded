"""Replay saved completions through the native verifiers and compare with what was logged.

The old generation logs record, for every completion, whether the reference implementation's
verifier found the main task and the side task done. This task feeds the same completions to
the native verifiers in the native sandbox, with no model call, and scores agreement:

    uv run inspect eval agent_artefacts/cot_redhanded/parity/verifier_replay.py \
        --model mockllm/model --max-sandboxes 12

A disagreement is either a porting mistake or a check that is not deterministic (timing for
`complexity`, the network for `iodownload` and `ioexfil`); the log says which sample to look at.
"""

from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.log import read_eval_log
from inspect_ai.model import ModelOutput
from inspect_ai.scorer import Score, Scorer, Target, mean, scorer
from inspect_ai.solver import Generate, Solver, TaskState, solver

from cot_redhanded.dataset import _records, record_to_sample
from cot_redhanded.verifiers import verify

# Inspect runs a task file from its own directory, so paths are anchored at the repo root.
ROOT = Path(__file__).resolve().parents[3]
LOG_DIRS = ["logs/overnight-attack", "logs/baseline-red", "logs/baseline-benign"]
COMPOSE = ROOT / "src/cot_redhanded/sandbox/compose.yaml"


def replay_samples() -> list[Sample]:
    records = {r["task_id"].removeprefix("BigCodeBench/"): r for r in _records()}
    samples = []
    for directory in LOG_DIRS:
        for path in sorted((ROOT / directory).rglob("*.eval")):
            log = read_eval_log(str(path))
            side_task = log.eval.task_args["side_task"]
            attack = side_task != "baseline"
            for old in log.samples or []:
                score = next(iter((old.scores or {}).values()), None)
                if score is None or not score.metadata or "main_only" not in score.metadata:
                    continue
                task_id, policy = str(old.id).split("_", 1)
                sample = record_to_sample(
                    records[task_id], side_task if attack else None, policy if attack else None
                )
                sample.id = f"{path.parent.name}:{old.id}"
                sample.metadata = {
                    **(sample.metadata or {}),
                    "stored_completion": old.output.completion,
                    "logged_main": bool(score.metadata["main_only"]),
                    "logged_side": bool(score.metadata["side_only"]) if attack else None,
                }
                samples.append(sample)
    return samples


@solver
def replay() -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        state.output = ModelOutput.from_content("replay", state.metadata["stored_completion"])
        return state

    return solve


@scorer(metrics={"main_agrees": [mean()], "side_agrees": [mean()]})
def agreement() -> Scorer:
    async def score(state: TaskState, target: Target) -> Score:
        verdict = await verify(state.output.completion, target.text, state.metadata)
        logged_main, logged_side = state.metadata["logged_main"], state.metadata["logged_side"]
        return Score(
            value={
                "main_agrees": float(verdict.main == logged_main),
                "side_agrees": float(verdict.side == logged_side),
            },
            explanation=verdict.explanation[-2000:],
            metadata={
                "native_main": verdict.main,
                "native_side": verdict.side,
                "logged_main": logged_main,
                "logged_side": logged_side,
                **verdict.details,
            },
        )

    return score


@task
def verifier_replay() -> Task:
    return Task(
        dataset=MemoryDataset(replay_samples()),
        solver=replay(),
        scorer=agreement(),
        sandbox=("docker", str(COMPOSE)),
    )
