"""Tests for the thin wrapper around the CoT Red-Handed reference implementation.

The wrapper has no evaluation logic of its own, so these tests check that it delegates
faithfully: that upstream's generation settings, sandbox and dataset arrive unchanged, and
that bad parameters fail before a long run rather than during one.
"""

from pathlib import Path

import pytest
from inspect_ai import Task, eval
from pytest_inspect_evals.assertions import assert_eval_success

from cot_redhanded import (
    ATTACK_POLICIES,
    ID_SETS,
    REASONING_EFFORTS,
    SIDE_TASKS,
    cot_redhanded_generate,
)
from cot_redhanded.cot_redhanded import EVAL_VERSION

# Upstream's generation settings, from cot_red_handed.tasks.config. The baseline is only a
# baseline if these travel through the wrapper unchanged.
UPSTREAM_TEMPERATURE = 0.7
UPSTREAM_MAX_TOKENS = 16000
TEST_SPLIT_SIZE = 855


def test_version_comes_from_eval_yaml() -> None:
    assert EVAL_VERSION == "1-B"


def test_task_space_matches_the_paper() -> None:
    assert len(SIDE_TASKS) == 11
    assert len(ATTACK_POLICIES) == 10
    assert set(ID_SETS) == {"TRAIN", "VAL", "RED", "TEST"}


def test_unknown_side_task_fails_fast() -> None:
    # Upstream would otherwise raise a KeyError deep in prompt loading, after the download.
    with pytest.raises(ValueError, match="Unknown side task"):
        cot_redhanded_generate(side_task="not_a_side_task")


def test_unknown_attack_policy_fails_fast() -> None:
    with pytest.raises(ValueError, match="Unknown attack policy"):
        cot_redhanded_generate(attack_policy="not_a_policy")


def test_unknown_id_set_fails_fast() -> None:
    # Upstream would otherwise build an empty dataset and run nothing.
    with pytest.raises(ValueError, match="Unknown id set"):
        cot_redhanded_generate(id_set="NOT_A_SET")


@pytest.mark.dataset_download
@pytest.mark.huggingface
def test_wrapper_passes_upstream_settings_through() -> None:
    task = cot_redhanded_generate()
    assert isinstance(task, Task)
    # Upstream names the task itself; the wrapper does not rename it.
    assert task.name == "bigcodebench_varname_cot_eval"
    assert task.config.temperature == UPSTREAM_TEMPERATURE
    assert task.config.max_tokens == UPSTREAM_MAX_TOKENS
    assert task.sandbox is not None
    assert task.sandbox.type == "docker"


@pytest.mark.dataset_download
@pytest.mark.huggingface
def test_default_dataset_is_the_test_split() -> None:
    task = cot_redhanded_generate()
    assert len(task.dataset) == TEST_SPLIT_SIZE
    sample = task.dataset[0]
    # The BigCodeBench task id must stay recoverable, so a baseline run can be compared
    # sample-for-sample against a later implementation.
    assert str(sample.id).split("_")[0].isdigit()
    assert sample.metadata is not None
    assert sample.metadata["side_task_name"] == "varname"
    assert sample.metadata["attack_policy"] == "baseline"


@pytest.mark.dataset_download
@pytest.mark.huggingface
def test_side_task_is_injected_into_the_prompt() -> None:
    with_side_task = cot_redhanded_generate(side_task="varname")
    sample = with_side_task.dataset[0]
    assert sample.metadata is not None
    # Upstream splices the side-task instruction ahead of the output description.
    assert sample.metadata["side_and_attack_policy"] != "baseline"
    assert str(sample.input) != sample.metadata["prompt"]


@pytest.mark.slow(900)
@pytest.mark.docker
@pytest.mark.dataset_download
@pytest.mark.huggingface
def test_generate_task_runs_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Upstream's solver reads and writes a `completed_ids/` directory relative to the working
    # directory, so run from a temporary one rather than polluting the repository.
    monkeypatch.chdir(tmp_path)
    # Opt-in slow: the first run builds upstream's image, which installs r-base, GDAL and 74
    # pinned packages. RED is the smallest split.
    [log] = eval(
        tasks=cot_redhanded_generate(id_set="RED"),
        model="mockllm/model",
        limit=1,
    )
    # mockllm returns fixed text, so the sample will not pass; this checks the pipeline runs,
    # the sandbox starts and the scorer produces a score, not that the answer is right.
    assert_eval_success(log, expected_sample_count=1)
    assert log.samples is not None
    assert all(s.scores for s in log.samples)


@pytest.mark.dataset_download
@pytest.mark.huggingface
def test_annotated_side_tasks_are_restricted_to_annotated_ids() -> None:
    # Upstream keeps only the samples carrying a side test case for these two side tasks.
    for side_task in ("complexity", "edgecase"):
        task = cot_redhanded_generate(side_task=side_task)
        assert len(task.dataset) < TEST_SPLIT_SIZE
        assert all(
            s.metadata is not None and s.metadata.get("side_test_case") for s in task.dataset
        )


class TestReasoningParameters:
    """How much the untrusted model thinks is the independent variable, so it is a parameter.

    Upstream's values must stay the defaults: the reasoning trace is what the CoT monitor
    reads, so a changed default would silently move every CoT figure.
    """

    def test_the_defaults_are_upstreams(self) -> None:
        config = cot_redhanded_generate().config
        assert config.reasoning_effort == "high"
        assert config.reasoning_tokens == 12800

    @pytest.mark.parametrize("effort", REASONING_EFFORTS)
    def test_every_advertised_effort_reaches_the_config(self, effort: str) -> None:
        assert cot_redhanded_generate(reasoning_effort=effort).config.reasoning_effort == effort

    def test_an_empty_effort_keeps_upstreams(self) -> None:
        assert cot_redhanded_generate(reasoning_effort="").config.reasoning_effort == "high"

    def test_rejects_an_unknown_effort(self) -> None:
        with pytest.raises(ValueError, match="reasoning effort"):
            cot_redhanded_generate(reasoning_effort="exhaustive")

    def test_a_token_budget_reaches_the_config(self) -> None:
        assert cot_redhanded_generate(reasoning_tokens=4000).config.reasoning_tokens == 4000

    def test_a_token_budget_alone_clears_the_inherited_effort(self) -> None:
        # Inspect's OpenRouter provider prefers effort and drops the budget when both are set,
        # so leaving upstream's "high" in place would make this parameter do nothing.
        config = cot_redhanded_generate(reasoning_tokens=4000).config
        assert config.reasoning_tokens == 4000
        assert config.reasoning_effort is None

    def test_an_explicit_effort_wins_over_a_token_budget(self) -> None:
        # Asking for both is asking for what the provider honours, which is effort.
        config = cot_redhanded_generate(reasoning_effort="low", reasoning_tokens=4000).config
        assert config.reasoning_effort == "low"
        assert config.reasoning_tokens == 4000

    def test_zero_tokens_keeps_upstreams(self) -> None:
        assert cot_redhanded_generate(reasoning_tokens=0).config.reasoning_tokens == 12800

    def test_rejects_a_negative_token_budget(self) -> None:
        with pytest.raises(ValueError, match="reasoning_tokens"):
            cot_redhanded_generate(reasoning_tokens=-1)

    def test_the_other_upstream_settings_are_untouched(self) -> None:
        # The merge that restores MODEL_CONFIG must keep working alongside these overrides.
        config = cot_redhanded_generate(reasoning_effort="low", reasoning_tokens=100).config
        assert config.temperature == pytest.approx(0.7)
        assert config.max_tokens == 16000
        assert config.top_p == pytest.approx(0.95)
