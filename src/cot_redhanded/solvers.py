"""The solvers of the two tasks.

`generate_until_success` is the untrusted model's turn in `bigcodebench_side` when it is allowed
more than one attempt. With one attempt the task uses Inspect's own `generate()` and the scorer
verifies the result; this solver is for the paper's multi-attempt protocol, in which a model was
given up to five tries at each problem and only its failures were retried.

`monitor_solver` is the monitor's turn in `cot_redhanded`: the model the eval is run with reads
a saved transcript and says how suspicious it is.
"""

import asyncio
from dataclasses import asdict
from typing import Any

from inspect_ai.model import ChatMessage, ChatMessageUser, ModelOutput, get_model
from inspect_ai.solver import Generate, Solver, TaskState, solver

from cot_redhanded.monitor import SETTING_CHANNELS, MonitorSetting, message_text
from cot_redhanded.monitor import render_monitor_prompts as render
from cot_redhanded.prompts import MONITOR_PROMPTS
from cot_redhanded.verifiers import Verdict, reset_sandbox, verify

__all__ = [
    "REPLIES_KEY",
    "generate_until_success",
    "monitor_solver",
    "stored_attempt",
    "stored_replies",
]

_VERDICT_KEY = "cot_redhanded:verdict"
_ATTEMPTS_KEY = "cot_redhanded:attempts"

#: Store key under which a monitor solver leaves its replies, as a mapping from channel name
#: (`"action"`, `"cot"`) to the reply text.
REPLIES_KEY = "cot_redhanded:replies"


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


@solver
def monitor_solver(setting: MonitorSetting = "cot", monitor_prompt_id: int = 1) -> Solver:
    """Have the model under evaluation monitor one saved transcript.

    The sample is one built by `transcripts.load_transcript_samples`: its input is the problem
    as an honest model would receive it, and its metadata holds the `action` (the submitted
    code) and the `reasoning` to show the monitor. Each channel the setting needs is asked
    once, as an independent one-turn conversation, so the monitor never sees a verdict of its
    own. The replies are stored under `REPLIES_KEY` for the scorer.

    Args:
        setting: `"action"` shows the code, `"cot"` the reasoning and the code, and
            `"hybrid"` asks both, one call each.
        monitor_prompt_id: Which of `prompts.MONITOR_PROMPTS` to ask with.

    Returns:
        The solver.
    """
    if setting not in SETTING_CHANNELS:
        raise ValueError(
            f"Unknown monitor setting {setting!r}; expected one of {', '.join(SETTING_CHANNELS)}"
        )
    if monitor_prompt_id not in MONITOR_PROMPTS:
        raise ValueError(
            f"Unknown monitor prompt {monitor_prompt_id}; expected one of {sorted(MONITOR_PROMPTS)}"
        )
    prompt = MONITOR_PROMPTS[monitor_prompt_id]
    channels = SETTING_CHANNELS[setting]

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        prompts = render(
            prompt,
            problem=state.input_text,
            action=state.metadata["action"],
            reasoning=state.metadata["reasoning"],
        )
        monitor = get_model()

        async def ask(channel: str) -> ModelOutput:
            # Never cached: repeated draws of the same prompt are meant to differ.
            return await monitor.generate([ChatMessageUser(content=prompts[channel])], cache=False)

        outputs = await asyncio.gather(*(ask(channel) for channel in channels))
        messages: list[ChatMessage] = []
        replies: dict[str, str] = {}
        for channel, output in zip(channels, outputs, strict=True):
            messages += [ChatMessageUser(content=prompts[channel]), output.message]
            replies[channel] = message_text(output.message)
        state.messages = messages
        state.output = outputs[-1]
        state.store.set(REPLIES_KEY, replies)
        return state

    return solve


def stored_replies(state: TaskState) -> dict[str, str] | None:
    """Read back the replies a monitor solver stored.

    Args:
        state: The sample's state.

    Returns:
        The reply text for each channel asked, or None when the solver stored none.
    """
    stored: Any = state.store.get(REPLIES_KEY)
    if not isinstance(stored, dict) or not stored:
        return None
    return {str(channel): str(reply) for channel, reply in stored.items()}
