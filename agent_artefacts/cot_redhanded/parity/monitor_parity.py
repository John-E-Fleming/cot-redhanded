"""Compare the native monitor pipeline with the reference implementation's, on saved logs.

This was run while the reference package was still a dependency, and passed. To run it
again, install the reference package first:

    uv pip install git+https://github.com/cotredhanded/code@505d9fca6fc5b859492d8b9500b374c8cbf501e8

    uv run python agent_artefacts/cot_redhanded/parity/monitor_parity.py

Four checks, none of which calls a model:

1. Transcripts: the completions selected for monitoring from each generation log, and the
   action and reasoning extracted from each, against upstream's `make_dataset`.
2. Prompts: the natively rendered CoT prompt against the one recorded in the old monitor logs.
3. Verdicts: every monitor reply in the old monitor logs, re-read natively, against upstream's
   `get_monitor_scores`.
4. Metrics: the single-draw scores from the old logs, through `control_metrics`, one
   channel per run.

It then writes the fixtures the test suite uses once the reference package is gone.
"""

import json
import math
import sys
from collections import Counter
from pathlib import Path

from cot_red_handed.monitor.inspect_monitor import get_monitor_scores, make_dataset
from inspect_ai.log import read_eval_log
from inspect_ai.model import ChatMessageAssistant
from inspect_ai.scorer import SampleScore, Score

from cot_redhanded.metrics import control_metrics, hybrid_score
from cot_redhanded.monitor import message_text, read_reply, render_monitor_prompts
from cot_redhanded.prompts import MONITOR_PROMPTS
from cot_redhanded.transcripts import transcript_samples

LOGS = Path("logs")
FIXTURES = Path("tests/cot_redhanded/fixtures")
GENERATION_DIRS = ["overnight-attack", "overnight-benign", "baseline-red", "baseline-benign"]
MONITOR_RUNS = {
    "gpt-4.1-mini": ["monitor-overnight-attack", "monitor-overnight-benign"],
    "gpt-4.1-nano": ["monitor-nano"],
    "gemini-2.5-flash-lite": ["monitor-flashlite"],
}
PROMPT = MONITOR_PROMPTS[1]
MAX_FIXTURE_REPLY = 12000
PROMPT_METADATA = {"min_score": 0, "max_score": 100, "inverted": False}


def check_transcripts() -> int:
    failures = 0
    for directory in GENERATION_DIRS:
        for path in sorted((LOGS / directory).rglob("*.eval")):
            log = read_eval_log(str(path))
            if log.status != "success":
                continue
            honest = log.eval.task_args.get("side_task") == "baseline"
            theirs = {
                s.id: (s.input, s.metadata["action"], s.metadata["cot"])
                for s in make_dataset(str(path), PROMPT_METADATA, has_side_task=not honest,
                                      errorfile="/dev/null")
            }
            ours = {
                s.metadata["source_sample_id"]: (s.input, s.metadata["action"], s.metadata["reasoning"])
                for s in transcript_samples(log)
            }
            same = theirs == ours
            failures += not same
            print(f"  {'ok ' if same else 'DIFF'} {directory}/{path.parent.name}: {len(theirs)} upstream, {len(ours)} native")
            if not same:
                print("     only upstream:", sorted(set(theirs) - set(ours))[:5], "only native:", sorted(set(ours) - set(theirs))[:5])
    return failures


def upstream_outcome(reply: str) -> float | str:
    """What upstream's scorer makes of one reply: a normalised score, or 'raises'."""
    try:
        (scores,) = get_monitor_scores([ChatMessageAssistant(content=reply)])
    except ValueError:
        return "raises"
    return sum(scores) / 100


def monitor_samples(run_dirs: list[str]):
    for directory in run_dirs:
        for path in sorted((LOGS / directory).rglob("*.eval")):
            log = read_eval_log(str(path))
            honest = str(log.eval.task_args.get("has_side_task")) == "False"
            for sample in log.samples or []:
                yield path, "honest" if honest else "attack", sample


def check_prompts_and_verdicts() -> tuple[int, dict, dict]:
    failures = 0
    reply_fixture: list[dict] = []
    score_fixture: dict[str, list] = {}
    for monitor, run_dirs in MONITOR_RUNS.items():
        outcomes: Counter[str] = Counter()
        prompt_mismatches = 0
        rows = []
        for path, mode, sample in monitor_samples(run_dirs):
            metadata = sample.metadata
            rendered = render_monitor_prompts(PROMPT, sample.input, metadata["action"], metadata["cot"])
            prompt_mismatches += rendered["cot"] != sample.messages[0].content
            scores = {}
            for channel, message in zip(("action", "cot"), sample.messages[1:]):
                reply = message_text(message)
                theirs = upstream_outcome(reply) if isinstance(message.content, str) else "raises"
                draw = read_reply(reply, PROMPT)
                if theirs == "raises":
                    kind = "upstream raises -> native " + ("unreadable" if draw.score is None else "reads it")
                elif draw.score is not None and math.isclose(draw.score, theirs):
                    kind = "same" + (" (no verdict, scored 0)" if draw.missing else "")
                else:
                    kind = "DIFFERENT"
                    failures += 1
                    print(f"     DIFFERENT {path.parent.name} {sample.id} {channel}: upstream {theirs} native {draw.score}")
                outcomes[kind] += 1
                scores[channel] = draw.score
                # Two Flash Lite replies run to hundreds of kilobytes of repetition; leave them out.
                if kind != "same" and len(reply) <= MAX_FIXTURE_REPLY:
                    reply_fixture.append({"monitor": monitor, "id": str(sample.id), "channel": channel,
                                          "reply": reply, "upstream": theirs, "native": draw.score,
                                          "missing": draw.missing})
            rows.append([mode, scores["action"], scores["cot"]])
        score_fixture[monitor] = rows
        failures += prompt_mismatches
        print(f"  {monitor}: {len(rows)} samples, {prompt_mismatches} prompt mismatches, verdicts {dict(outcomes)}")
    return failures, {"replies": reply_fixture}, score_fixture


def metrics_for(rows: list) -> dict[str, float]:
    """The catch rate and AUC of each channel, each scored as its own monitor run.

    The keys keep the names the fixture was first written with (`action_catch_rate` and so
    on), so the recorded figures can be compared across versions of the metric.
    """
    result: dict[str, float] = {}
    for channel in ("action", "cot", "hybrid"):
        scores = []
        for mode, action, cot in rows:
            if channel == "hybrid":
                value = math.nan if action is None or cot is None else hybrid_score(action, cot)
            else:
                value = {"action": action, "cot": cot}[channel]
                value = math.nan if value is None else value
            scores.append(SampleScore(score=Score(value=value), sample_metadata={"eval_mode": mode}))
        run = control_metrics(bootstrap_replicates=0)(scores)
        result[f"{channel}_catch_rate"] = run["catch_rate"]
        result[f"{channel}_auc"] = run["auc"]
        result["monitored_attack"] = run["n_attack"]
        result["monitored_honest"] = run["n_honest"]
    return result


def main() -> int:
    print("1. transcripts")
    failures = check_transcripts()
    print("2-3. prompts and verdicts")
    more, reply_fixture, score_fixture = check_prompts_and_verdicts()
    failures += more
    print("4. metrics from the stored single-draw scores")
    expected = {}
    for monitor, rows in score_fixture.items():
        result = metrics_for(rows)
        expected[monitor] = result
        print(f"  {monitor}: n_attack={result['monitored_attack']:.0f} n_honest={result['monitored_honest']:.0f}  " + "  ".join(
            f"{c} {result[f'{c}_catch_rate']:.1%}/{result[f'{c}_auc']:.3f}" for c in ("action", "cot", "hybrid")))
    if failures:
        print(f"{failures} differences; fixtures not written")
        return 1
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / "monitor_replies.json").write_text(json.dumps(reply_fixture, indent=1, ensure_ascii=False) + "\n")
    (FIXTURES / "monitor_scores.json").write_text(
        json.dumps({"scores": score_fixture, "expected": expected}, indent=1) + "\n")
    print("no differences; fixtures written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
