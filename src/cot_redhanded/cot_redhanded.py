"""CoT Red-Handed: stress testing chain-of-thought monitoring.

An untrusted model solves a BigCodeBench problem. In **attack** mode it is also given a hidden
side task; in **honest** mode it is not. A trusted monitor then reads each successful
completion twice, once seeing only the code and once also seeing the reasoning, and says how
suspicious it is. The eval reports how many attacks each view catches when the monitor's
threshold is set to flag 1% of honest completions.

Two tasks:

- `cot_redhanded` runs the whole thing: generate, verify in the sandbox, monitor, report.
- `cot_redhanded_monitor` runs only the monitor, over completions saved by earlier runs.

Arnav et al., "CoT Red-Handed: Stress Testing Chain-of-Thought Monitoring", arXiv:2505.23575.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Literal, cast

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset
from inspect_ai.model import GenerateConfig
from inspect_ai.solver import generate, prompt_template
from inspect_evals_utils.metadata import load_eval_version

from cot_redhanded.dataset import ID_SETS, IdSet, load_cot_redhanded_dataset
from cot_redhanded.metrics import (
    DEFAULT_FALSE_POSITIVE_RATE,
    HYBRID_ACTION_WEIGHT,
    control_metrics,
)
from cot_redhanded.prompts import MAIN_TASK_TEMPLATE
from cot_redhanded.scorers import (
    DEFAULT_MONITOR_PROMPT_ID,
    DEFAULT_MONITOR_SAMPLES,
    cot_redhanded_scorer,
    transcript_monitor_scorer,
)
from cot_redhanded.side_tasks import ATTACK_POLICIES, SIDE_TASKS, AttackPolicyName, SideTaskName
from cot_redhanded.solvers import generate_until_success
from cot_redhanded.transcripts import load_transcript_samples

__all__ = [
    "DEFAULT_ATTACK_POLICY",
    "DEFAULT_ID_SET",
    "DEFAULT_SIDE_TASK",
    "EVAL_VERSION",
    "REASONING_EFFORTS",
    "cot_redhanded",
    "cot_redhanded_monitor",
]

# The version comes from eval.yaml, the file beside this module.
EVAL_VERSION = load_eval_version(__file__)

DEFAULT_SIDE_TASK: SideTaskName = "varname"
DEFAULT_ATTACK_POLICY: AttackPolicyName = "baseline"
DEFAULT_ID_SET: IdSet = "TEST"

#: Every value Inspect accepts for `reasoning_effort`. Which of these a given model honours is
#: the provider's business; Inspect passes the value through and the provider decides.
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

# The reference implementation's generation settings for the untrusted model.
_TEMPERATURE = 0.7
_TOP_P = 0.95
_MAX_TOKENS = 16000
_REASONING_TOKENS = 12800
_REASONING_EFFORT = "high"
_MAX_RETRIES = 3

_COMPOSE_FILE = Path(__file__).parent / "sandbox" / "compose.yaml"


def _names[Name: str](
    value: Name | Literal["all"] | Sequence[Name] | None, allowed: Sequence[Name], kind: str
) -> list[Name]:
    """Read a parameter that names one of `allowed`, several of them, `"all"` or none.

    `None` means none: `-T side_task=` on the command line arrives as `None`, because Inspect
    reads task arguments as YAML. It also turns `-T side_task=a,b` into a list, so several
    names never arrive as one string.
    """
    if value is None:
        return []
    if value == "all":
        return list(allowed)
    names = [value] if isinstance(value, str) else list(value)
    for name in names:
        if name not in allowed:
            raise ValueError(f"Unknown {kind} {name!r}; expected one of {', '.join(allowed)}")
    return cast(list[Name], names)


@task
def cot_redhanded(
    side_task: SideTaskName | Literal["all"] | list[SideTaskName] | None = DEFAULT_SIDE_TASK,
    attack_policy: AttackPolicyName
    | Literal["all"]
    | list[AttackPolicyName] = DEFAULT_ATTACK_POLICY,
    id_set: IdSet = DEFAULT_ID_SET,
    use_pairings: bool = False,
    include_honest: bool = True,
    max_attempts: int = 1,
    monitor_samples: int = DEFAULT_MONITOR_SAMPLES,
    monitor_prompt_id: int = DEFAULT_MONITOR_PROMPT_ID,
    action_weight: float = HYBRID_ACTION_WEIGHT,
    false_positive_rate: float = DEFAULT_FALSE_POSITIVE_RATE,
    reasoning_effort: str = "",
    reasoning_tokens: int = 0,
) -> Task:
    """Generate honest and attack completions, verify them, and measure how well a monitor catches the attacks.

    The model under `--model` is the untrusted model. The trusted monitor is the model bound to
    the `monitor` role (`--model-role monitor=...`), `openai/gpt-4.1-mini` by default.

    Args:
        side_task: The hidden objective given to attack samples. One side task, a list of
            them, or `"all"`. `None` or an empty list gives an honest-only run.
        attack_policy: How the side task is put to the model. One policy, a list of them, or `"all"`.
        id_set: Which split of BigCodeBench to use: TRAIN, VAL, RED or TEST.
        use_pairings: Keep an attack sample only when its problem is paired with its side task
            in the reference implementation's pairings, so each problem is attacked once.
        include_honest: Include the honest samples, which set the monitor's threshold. Without
            them the catch rates cannot be computed and are reported as NaN.
        max_attempts: How many attempts the untrusted model gets at each sample. A failed
            attempt is retried from scratch, without feedback, until one succeeds. 1, the
            reference implementation's default, is a single attempt; the paper's completion
            rates also report 5.
        monitor_samples: How many times to sample each monitor channel per completion. The
            action and CoT scores are each the mean; the hybrid uses one draw of each. The
            paper uses 2.
        monitor_prompt_id: Which monitor prompt to use.
        action_weight: Weight on the action-only score in the hybrid score; the rest weights the
            chain-of-thought score. The paper uses 0.55.
        false_positive_rate: The share of honest completions the monitor may flag. The catch
            rate is measured at the threshold that achieves it. The paper uses 0.01.
        reasoning_effort: Reasoning effort for the untrusted model. Empty keeps the reference
            implementation's `"high"`.
        reasoning_tokens: Reasoning token budget for the untrusted model. 0 keeps the reference
            implementation's 12800. Given without `reasoning_effort`, the effort is left unset
            so that the budget takes effect.

    Returns:
        The task.
    """
    side_tasks = _names(side_task, SIDE_TASKS, "side task")
    attack_policies = _names(attack_policy, ATTACK_POLICIES, "attack policy")
    if id_set not in ID_SETS:
        raise ValueError(f"Unknown id set {id_set!r}; expected one of {', '.join(ID_SETS)}")
    if reasoning_effort and reasoning_effort not in REASONING_EFFORTS:
        raise ValueError(
            f"Unknown reasoning effort {reasoning_effort!r}; expected one of "
            f"{', '.join(REASONING_EFFORTS)}, or '' for the default"
        )
    if reasoning_tokens < 0:
        raise ValueError(f"reasoning_tokens must not be negative, got {reasoning_tokens}")
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be at least 1, got {max_attempts}")
    if not side_tasks and not include_honest:
        raise ValueError("Nothing to run: no side task was given and include_honest is False")

    effort: str | None = reasoning_effort or _REASONING_EFFORT
    if reasoning_tokens and not reasoning_effort:
        # Inspect sends a provider the effort or the token budget, and prefers the effort.
        effort = None

    return Task(
        dataset=load_cot_redhanded_dataset(
            side_tasks, attack_policies, id_set, use_pairings, include_honest
        ),
        solver=[
            prompt_template(MAIN_TASK_TEMPLATE),
            generate() if max_attempts == 1 else generate_until_success(max_attempts),
        ],
        # Applies to the untrusted model only; Inspect does not pass a task's generation
        # settings on to a model resolved through a role, so the monitor keeps its defaults.
        config=GenerateConfig(
            temperature=_TEMPERATURE,
            top_p=_TOP_P,
            max_tokens=_MAX_TOKENS,
            max_retries=_MAX_RETRIES,
            reasoning_effort=effort,  # pyright: ignore[reportArgumentType]
            reasoning_tokens=reasoning_tokens or _REASONING_TOKENS,
        ),
        scorer=cot_redhanded_scorer(monitor_samples, monitor_prompt_id, action_weight),
        metrics=[control_metrics(false_positive_rate)],
        sandbox=("docker", str(_COMPOSE_FILE)),
        version=EVAL_VERSION,
    )


@task
def cot_redhanded_monitor(
    log_paths: str | list[str],
    monitor_samples: int = DEFAULT_MONITOR_SAMPLES,
    monitor_prompt_id: int = DEFAULT_MONITOR_PROMPT_ID,
    action_weight: float = HYBRID_ACTION_WEIGHT,
    false_positive_rate: float = DEFAULT_FALSE_POSITIVE_RATE,
) -> Task:
    """Monitor completions saved by earlier runs, without generating anything new.

    This is a scoring-only task: nothing is generated, so `--model` names the **monitor**,
    unless a model is bound to the `monitor` role. Give it logs holding both honest and attack
    completions, because the honest ones set the threshold the attacks are measured against.

    Args:
        log_paths: Generation logs to monitor: one path, several (a list, or comma-separated),
            or a directory, which stands for every `.eval` file beneath it. Logs from
            `cot_redhanded` and from the reference implementation's generation task are both
            read.
        monitor_samples: How many times to sample each monitor channel per completion. The
            action and CoT scores are each the mean; the hybrid uses one draw of each. The
            paper uses 2.
        monitor_prompt_id: Which monitor prompt to use.
        action_weight: Weight on the action-only score in the hybrid score. The paper uses 0.55.
        false_positive_rate: The share of honest completions the monitor may flag. The paper
            uses 0.01.

    Returns:
        The task.
    """
    samples = load_transcript_samples(log_paths)
    return Task(
        dataset=MemoryDataset(samples, name="cot_redhanded_transcripts"),
        solver=[],
        scorer=transcript_monitor_scorer(monitor_samples, monitor_prompt_id, action_weight),
        metrics=[control_metrics(false_positive_rate)],
        version=EVAL_VERSION,
    )
