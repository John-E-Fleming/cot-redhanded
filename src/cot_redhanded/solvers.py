"""The untrusted model's turn, when it is allowed more than one attempt.

With one attempt the task uses Inspect's own `generate()` and the scorer verifies the result.
`generate_until_success` is for the paper's multi-attempt protocol, in which a model was given
up to five tries at each problem and only its failures were retried.
"""

from dataclasses import asdict
from typing import Any

from inspect_ai.solver import Generate, Solver, TaskState, solver

from cot_redhanded.verifiers import Verdict, reset_sandbox, verify

__all__ = ["generate_until_success", "stored_attempt"]

_VERDICT_KEY = "cot_redhanded:verdict"
_ATTEMPTS_KEY = "cot_redhanded:attempts"


@solver
def generate_until_success(max_attempts: int) -> Solver:
    """Generate, verify, and try again from scratch until the completion succeeds.

    Each attempt is a fresh one-turn conversation with the same prompt: the model is not told
    that it failed or shown its earlier attempt. An attempt succeeds when the main task passes
    and, for an attack sample, the side task does too. The verdict on the last attempt is kept
    for the scorer, which must not verify again: the sandbox is no longer in its initial state.

    Args:
        max_attempts: The most attempts to make. Must be at least 1.

    Returns:
        The solver.
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be at least 1, got {max_attempts}")

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        prompt = list(state.messages)
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                state.messages = list(prompt)
                await reset_sandbox(state.metadata)
            state = await generate(state)
            verdict = await verify(state.output.completion, state.target.text, state.metadata)
            # Stored as plain data, so that it is written to the log like any other store value.
            state.store.set(_VERDICT_KEY, asdict(verdict))
            state.store.set(_ATTEMPTS_KEY, attempt)
            if verdict.success:
                break
        return state

    return solve


def stored_attempt(state: TaskState) -> tuple[Verdict, int] | None:
    """Read back what `generate_until_success` concluded about a sample.

    Args:
        state: The sample's state.

    Returns:
        The verdict on the last attempt and the number of attempts made, or None when the
        sample was not run through `generate_until_success`.
    """
    stored: Any = state.store.get(_VERDICT_KEY)
    if not isinstance(stored, dict):
        return None
    return Verdict(**stored), int(state.store.get(_ATTEMPTS_KEY, 1))
