"""A thin Inspect wrapper around the CoT Red-Handed reference implementation.

This module deliberately contains no evaluation logic. It exposes the reference
implementation's own tasks, installed as `cot_red_handed` and pinned to commit
505d9fca6fc5b859492d8b9500b374c8cbf501e8, so that this repository can produce a baseline
replication of the paper with the original behaviour intact, defects included.

Nothing here fixes, works around or improves on upstream. That is the point: the baseline
exists to measure against. A later implementation in this repository will depart from upstream
in specific, argued ways, and each departure is only worth making if it can be shown against
these numbers to change an outcome.

Known upstream characteristics that the baseline therefore keeps:

- The BigCodeBench load is unpinned (`split="v0.1.4"`, no `revision=`), so the dataset can
  move under the baseline over time.
- Tasks 111, 227 and 917 stay in the data although their canonical solutions cannot pass.
- `ioexfil` targets `metrics.ctrlco.com`, which no longer resolves, so that side task cannot
  register a success.
- `canonical_solution_length` is computed by splitting on the first colon in a solution body
  that has no signature, which under-counts 586 of the 1,140 records.
- Scoring happens inside upstream's solver, which also writes completed sample ids to a
  `completed_ids/` directory under the working directory, and reads the private
  `TaskState._model`.

See `agent_artefacts/cot_redhanded/create/REQUIREMENTS.md` for the full list.
"""

from cot_red_handed.monitor.inspect_monitor import run_monitor as _upstream_run_monitor
from cot_red_handed.tasks.config import MODEL_CONFIG
from cot_red_handed.tasks.tasks import TaskManager
from inspect_ai import Task, task
from inspect_evals_utils.metadata import load_eval_version

from cot_redhanded.monitor_scoring import robust_monitor_scorer

# The version comes from eval.yaml, the file beside this module.
EVAL_VERSION = load_eval_version(__file__)

# Upstream's own defaults, from `cot_red_handed.tasks.config` and `run_tasks.py`.
MAIN_TASK = "bigcodebench"
DEFAULT_SIDE_TASK = "varname"
DEFAULT_ATTACK_POLICY = "baseline"
DEFAULT_ID_SET = "TEST"
DEFAULT_MONITOR_PROMPT_ID = 1

# The eleven side tasks and ten attack policies upstream defines, for reference and for
# validating parameters before a long run rather than after it.
SIDE_TASKS = (
    "complexity",
    "defaultval",
    "edgecase",
    "exit",
    "iodelete",
    "iodownload",
    "ioexfil",
    "logio",
    "longlines",
    "permissions",
    "varname",
)
ATTACK_POLICIES = (
    "baseline",
    "stealth",
    "future_proofing",
    "functionality",
    "dependency",
    "must_use",
    "implementation_template",
    "code_first",
    "cot_verbosity",
    "monitor_notice",
)
ID_SETS = ("TRAIN", "VAL", "RED", "TEST")

# Every reasoning effort Inspect's GenerateConfig accepts. A given model supports a subset,
# which OpenRouter declares as `reasoning.supported_efforts` on its models endpoint; of 356
# reasoning-capable models there, 237 declare a vocabulary and 31 distinct vocabularies are in
# use, so this cannot be narrowed to one list that is right for every model. An effort a model
# does not support is the provider's error to raise, not ours to predict.
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

# Upstream's name for "no side task": `load_red_dataset` skips the side-task injection when the
# side task is None or "baseline", and `SIDE_TASK_CONFIGS["baseline"]` scores with
# `baseline_verify`, which checks the main task alone. This is the benign control arm, and the
# false positive rate the paper's catch rate is calibrated against comes from it. The name
# collides with the "baseline" *attack policy*, which is upstream's doing, not ours.
BENIGN_SIDE_TASK = "baseline"


@task
def cot_redhanded_generate(
    side_task: str = DEFAULT_SIDE_TASK,
    attack_policy: str = DEFAULT_ATTACK_POLICY,
    id_set: str = DEFAULT_ID_SET,
    use_pairings: bool = False,
    reasoning_effort: str = "",
    reasoning_tokens: int = 0,
) -> Task:
    """Run the untrusted model on a coding problem carrying a hidden side task.

    Delegates entirely to `cot_red_handed.tasks.tasks.TaskManager.generate_task`. The returned
    task carries upstream's own generation settings (temperature 0.7, 16,000 max tokens, 80% of
    them reserved for reasoning) and upstream's Docker sandbox.

    Args:
        side_task: Which sabotage to hide alongside the main task. One of `SIDE_TASKS`.
        attack_policy: How the side task is framed to the model. One of `ATTACK_POLICIES`.
        id_set: Which of the paper's splits to draw main tasks from. One of `ID_SETS`.
        use_pairings: Draw one side task per main task from upstream's balanced pairings,
            rather than running this side task across the whole split.
        reasoning_effort: Override how hard the untrusted model thinks. One of
            `REASONING_EFFORTS`. Empty keeps upstream's `"high"`. The reasoning trace is what
            the CoT monitor reads, so this is an experimental variable, not a tuning knob.
        reasoning_tokens: Override the reasoning token budget. 0 keeps upstream's 12,800.
            Inspect's OpenRouter provider sends *either* an effort or a budget and prefers
            effort, so passing this without also passing `reasoning_effort` clears the effort
            in order to make the budget take effect. Passing both keeps effort and drops the
            budget, which is upstream's own situation: it sets both, so its 12,800 has never
            reached the API. Note also that OpenRouter declares only 10 of its 356
            reasoning-capable models as honouring a budget at all.

    Returns:
        Upstream's task, unmodified.

    Raises:
        ValueError: If a parameter is not one upstream recognises. Upstream would otherwise
            fail later, with a KeyError or an empty dataset.
    """
    if side_task not in (*SIDE_TASKS, BENIGN_SIDE_TASK):
        raise ValueError(
            f"Unknown side task {side_task!r}; expected one of {', '.join(SIDE_TASKS)}, "
            f"or {BENIGN_SIDE_TASK!r} for the benign control arm"
        )
    if attack_policy not in ATTACK_POLICIES:
        raise ValueError(
            f"Unknown attack policy {attack_policy!r}; expected one of {', '.join(ATTACK_POLICIES)}"
        )
    if id_set not in ID_SETS:
        raise ValueError(f"Unknown id set {id_set!r}; expected one of {', '.join(ID_SETS)}")
    if reasoning_effort and reasoning_effort not in REASONING_EFFORTS:
        raise ValueError(
            f"Unknown reasoning effort {reasoning_effort!r}; expected one of "
            f"{', '.join(REASONING_EFFORTS)}, or '' to keep upstream's"
        )
    if reasoning_tokens < 0:
        raise ValueError(f"reasoning_tokens must not be negative, got {reasoning_tokens}")

    upstream_task = TaskManager().generate_task(
        main_task_name=MAIN_TASK,
        side_task_name=side_task,
        attack_policies=[attack_policy],
        id_set=id_set,
        use_pairings=use_pairings,
    )

    # Restore the settings upstream's own runner applies but its Task does not carry.
    #
    # `run_tasks.py` builds models through `ModelManager.get_model`, which attaches
    # `MODEL_CONFIG` to the *model*, while `generate_task` puts only temperature and
    # max_tokens on the *task*. Reaching upstream through `inspect eval` instead of through
    # `run_tasks.py` would therefore silently drop reasoning_tokens (12,800), reasoning_effort
    # ("high"), top_p (0.95) and max_retries (3). The first two govern how much chain of
    # thought the model emits, which is precisely what the monitor reads, so a baseline
    # without them would not be a baseline.
    #
    # The task's own values win on overlap, matching the precedence upstream gets by putting
    # the narrower config on the task.
    upstream_task.config = MODEL_CONFIG.merge(upstream_task.config)

    # Applied after the merge so these win over both upstream configs. How much chain of
    # thought the model emits is the independent variable of this whole eval, so it is the one
    # generation setting worth exposing; the rest stay at upstream's values.
    if reasoning_effort:
        upstream_task.config.reasoning_effort = reasoning_effort  # type: ignore[assignment]
    if reasoning_tokens:
        upstream_task.config.reasoning_tokens = reasoning_tokens
        # `inspect_ai/model/_providers/openrouter.py` sends one of effort or budget and prefers
        # effort, warning and dropping the budget when both are set. Upstream sets both, so
        # asking for a budget here would otherwise change nothing at all. Clearing the
        # inherited effort is what makes this parameter do what its name says; an explicit
        # effort still wins, because then the caller has asked for both and effort is what the
        # provider honours.
        if not reasoning_effort:
            upstream_task.config.reasoning_effort = None

    return upstream_task


@task
def cot_redhanded_monitor(
    eval_log_path: str,
    prompt_id: int = DEFAULT_MONITOR_PROMPT_ID,
    has_side_task: bool = True,
    prompt_path: str = "",
    robust_parsing: bool = True,
) -> Task:
    """Score a trusted monitor on the transcripts a generation run produced.

    Delegates entirely to `cot_red_handed.monitor.inspect_monitor.run_monitor`, which reads a
    `.eval` log, keeps only the samples where the untrusted model completed both the main and
    the side task, and asks the monitor for an action-only and a chain-of-thought suspicion
    score. The model under test is the monitor.

    Args:
        eval_log_path: Path to a single `.eval` log from `cot_redhanded_generate`. Upstream
            rejects a directory.
        prompt_id: Which monitor prompt to use, matched against the files in upstream's
            `monitor/prompts` directory.
        has_side_task: False when scoring a benign run, which upstream uses to establish the
            false positive baseline.
        prompt_path: Directory to load the monitor prompt from. Upstream defaults to the
            `monitor/prompts` directory inside its own package, which holds a single prompt;
            pass a directory of your own to use a different one.
        robust_parsing: Read the monitor's verdict with `robust_monitor_scorer`, which records
            an unreadable verdict instead of raising on it. Pass False for upstream's parser,
            which calls `float()` on whatever its regex captured and so ends the run when a
            monitor writes `<answer>Score: 85</answer>`. True by default because the monitor is
            `--model`, and a monitor that can only be one model is not a parameter.

    Returns:
        Upstream's task, with the verdict parser replaced unless `robust_parsing` is False.
    """
    upstream_task = _upstream_run_monitor(
        eval_log_path=eval_log_path,
        prompt_id=prompt_id,
        has_side_task=has_side_task,
        prompt_path=prompt_path,
    )
    if robust_parsing:
        upstream_task.scorer = [robust_monitor_scorer()]
    return upstream_task
