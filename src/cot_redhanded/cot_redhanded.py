"""CoT Red-Handed: stress testing chain-of-thought monitoring.

An untrusted model solves a BigCodeBench problem. In **attack** mode it is also given a hidden
side task; in **honest** mode it is not. A trusted monitor then reads each successful
completion and says how suspicious it is, seeing only the code, or the reasoning as well. The
eval reports how many attacks the monitor catches when its threshold is set to flag 1% of
honest completions.

Two tasks, run one after the other:

- `bigcodebench_side` is run with the **untrusted model**. It generates the completions and
  verifies them in the sandbox: did the unit tests pass, and was the side task accomplished?
- `cot_redhanded` is run with the **monitor**. It reads the logs `bigcodebench_side` wrote and
  has the monitor score each successful completion.

Arnav et al., "CoT Red-Handed: Stress Testing Chain-of-Thought Monitoring", arXiv:2505.23575.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Literal, cast

from inspect_ai import Epochs, Task, task
from inspect_ai.dataset import MemoryDataset
from inspect_ai.solver import generate, prompt_template
from inspect_evals_utils.metadata import load_eval_version

from cot_redhanded.dataset import ID_SETS, IdSet, load_cot_redhanded_dataset
from cot_redhanded.metrics import (
    DEFAULT_FALSE_POSITIVE_RATE,
    HYBRID_ACTION_WEIGHT,
    GroupBy,
    control_metrics,
)
from cot_redhanded.monitor import MONITOR_SETTINGS, MonitorSetting
from cot_redhanded.prompts import MAIN_TASK_TEMPLATE
from cot_redhanded.scorers import (
    DEFAULT_MONITOR_PROMPT_ID,
    mean_of_readable,
    side_task_scorer,
    suspicion_scorer,
)
from cot_redhanded.side_tasks import ATTACK_POLICIES, SIDE_TASKS, AttackPolicyName, SideTaskName
from cot_redhanded.solvers import generate_until_success, monitor_solver
from cot_redhanded.transcripts import load_transcript_samples

__all__ = [
    "DEFAULT_ATTACK_POLICY",
    "DEFAULT_ID_SET",
    "DEFAULT_MONITOR_SETTING",
    "DEFAULT_SIDE_TASK",
    "EVAL_VERSION",
    "MONITOR_SAMPLES",
    "SandboxType",
    "bigcodebench_side",
    "cot_redhanded",
]

# The version comes from eval.yaml, the file beside this module.
EVAL_VERSION = load_eval_version(__file__)

DEFAULT_SIDE_TASK: SideTaskName = "varname"
DEFAULT_ATTACK_POLICY: AttackPolicyName = "baseline"
DEFAULT_ID_SET: IdSet = "TEST"
DEFAULT_MONITOR_SETTING: MonitorSetting = "cot"

#: How many times the paper draws from the monitor for each setting. The action-only and CoT
#: monitors are run twice and averaged; the hybrid uses a single run of each, so that all three
#: spend the same number of monitor calls.
MONITOR_SAMPLES: dict[str, int] = {"action": 2, "cot": 2, "hybrid": 1}

#: Where the untrusted model's code is run.
SandboxType = Literal["docker", "k8s"]

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
def bigcodebench_side(
    side_task: SideTaskName | Literal["all"] | list[SideTaskName] | None = DEFAULT_SIDE_TASK,
    attack_policy: AttackPolicyName
    | Literal["all"]
    | list[AttackPolicyName] = DEFAULT_ATTACK_POLICY,
    id_set: IdSet = DEFAULT_ID_SET,
    use_pairings: bool = False,
    include_honest: bool = True,
    max_attempts: int = 1,
    sandbox_type: SandboxType = "docker",
) -> Task:
    """Have an untrusted model solve BigCodeBench problems, with and without a hidden side task.

    The model under `--model` is the untrusted model. Each completion is verified in the
    sandbox: an honest sample on its unit tests, an attack sample on its unit tests and its
    side task. The log this task writes is the input to `cot_redhanded`.

    No sampling settings are set here. The paper's are in
    `run_configs/side_task_original.yaml`; without them the provider's defaults apply, and a
    model that returns no reasoning gives the CoT monitor nothing to read.

    Args:
        side_task: The hidden objective given to attack samples. One side task, a list of
            them, or `"all"`. `None` or an empty list gives an honest-only run.
        attack_policy: How the side task is put to the model. One policy, a list of them, or `"all"`.
        id_set: Which split of BigCodeBench to use: TRAIN, VAL, RED or TEST.
        use_pairings: Keep an attack sample only when its problem is paired with its side task
            in the reference implementation's pairings, so each problem is attacked once.
        include_honest: Include the honest samples, which set the monitor's threshold in
            `cot_redhanded`.
        max_attempts: How many attempts the untrusted model gets at each sample. A failed
            attempt is retried from scratch, without feedback, until one succeeds. 1, the
            reference implementation's default, is a single attempt; the paper's completion
            rates also report 5. Applies to honest and attack samples alike.
        sandbox_type: Where the code is run: `"docker"`, or `"k8s"` for a Kubernetes sandbox
            built from the same compose file.

    Returns:
        The task.
    """
    side_tasks = _names(side_task, SIDE_TASKS, "side task")
    attack_policies = _names(attack_policy, ATTACK_POLICIES, "attack policy")
    if id_set not in ID_SETS:
        raise ValueError(f"Unknown id set {id_set!r}; expected one of {', '.join(ID_SETS)}")
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be at least 1, got {max_attempts}")
    if not side_tasks and not include_honest:
        raise ValueError("Nothing to run: no side task was given and include_honest is False")

    return Task(
        dataset=load_cot_redhanded_dataset(
            side_tasks, attack_policies, id_set, use_pairings, include_honest
        ),
        solver=[
            prompt_template(MAIN_TASK_TEMPLATE),
            generate() if max_attempts == 1 else generate_until_success(max_attempts),
        ],
        scorer=side_task_scorer(),
        sandbox=(sandbox_type, str(_COMPOSE_FILE)),
        version=EVAL_VERSION,
    )


@task
def cot_redhanded(
    log_paths: str | list[str] | None = None,
    monitor: MonitorSetting = DEFAULT_MONITOR_SETTING,
    monitor_samples: int | None = None,
    monitor_prompt_id: int = DEFAULT_MONITOR_PROMPT_ID,
    action_weight: float = HYBRID_ACTION_WEIGHT,
    false_positive_rate: float = DEFAULT_FALSE_POSITIVE_RATE,
    group_by: GroupBy | None = None,
) -> Task:
    """Have a monitor score saved completions, and measure how many attacks it catches.

    The model under `--model` is the **monitor**. Nothing is generated by an untrusted model
    here: the completions come from logs written by `bigcodebench_side`. Give it logs holding
    both honest and attack completions, because the honest ones set the threshold the attacks
    are measured against.

    One run reports one monitor setting. Run it once each with `monitor=action`, `monitor=cot`
    and `monitor=hybrid` over the same logs to compare them. To try a monitor of your own,
    replace the solver with `task_with()`; see the README.

    Args:
        log_paths: Logs to monitor: one path, several (a list, or comma-separated), or a
            directory, which stands for every `.eval` file beneath it. Logs from
            `bigcodebench_side`, from earlier versions of this eval and from the reference
            implementation's generation task are all read.
        monitor: What the monitor is shown. `"action"` is the code alone, `"cot"` the
            reasoning and the code, and `"hybrid"` asks both and combines the two scores.
        monitor_samples: How many times to draw from the monitor per completion; the draws are
            epochs and their scores are averaged. Left unset, the paper's values are used: 2
            for `action` and `cot`, 1 for `hybrid`.
        monitor_prompt_id: Which monitor prompt to use.
        action_weight: Weight on the action-only score in the hybrid score; the rest weights the
            chain-of-thought score. The paper uses 0.55.
        false_positive_rate: The share of honest completions the monitor may flag. The catch
            rate is measured at the threshold that achieves it. The paper uses 0.01.
        group_by: Also report the catch rate per `"side_task"`, or for the paper's subtle and
            non-subtle groups with `"subtlety"`.

    Returns:
        The task.
    """
    if monitor not in MONITOR_SETTINGS:
        raise ValueError(
            f"Unknown monitor setting {monitor!r}; expected one of {', '.join(MONITOR_SETTINGS)}"
        )
    samples = MONITOR_SAMPLES[monitor] if monitor_samples is None else monitor_samples
    if samples < 1:
        raise ValueError(f"monitor_samples must be at least 1, got {samples}")
    if log_paths is None:
        raise ValueError(
            "log_paths is required: give the logs of a bigcodebench_side run to monitor"
        )

    return Task(
        dataset=MemoryDataset(load_transcript_samples(log_paths), name="cot_redhanded_transcripts"),
        solver=monitor_solver(monitor, monitor_prompt_id),
        scorer=suspicion_scorer(monitor_prompt_id, action_weight),
        metrics=[control_metrics(false_positive_rate, group_by=group_by)],
        epochs=Epochs(samples, mean_of_readable()),
        version=EVAL_VERSION,
    )
