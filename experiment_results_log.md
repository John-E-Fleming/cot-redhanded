# Experiment results log

A dated record of every evaluation run made while replicating [CoT Red-Handed](https://arxiv.org/abs/2505.23575) and porting it for `inspect_evals`.

**Conventions.** Newest entries go at the bottom. Each entry records what was run, the exact configuration needed to reproduce it, the numbers, and what was learned — including results that were negative, surprising, or contradicted an earlier assumption. Estimates are marked as such; everything else is measured.

______________________________________________________________________

## 2026-11-16 — Baseline wrapper, first runs

Branch `baseline-wrapper`, commit `9ce209a`. A thin Inspect wrapper around the reference implementation (`cot_red_handed`, pinned to `505d9fca6fc5b859492d8b9500b374c8cbf501e8`), with no bug fixes, to establish a baseline the later port can be measured against.

### Setup

|                 |                                                                                           |
| --------------- | ----------------------------------------------------------------------------------------- |
| Untrusted model | `openrouter/deepseek/deepseek-r1-0528`                                                    |
| Provider        | DeepInfra, **fp4**, pinned via `provider={"order":["DeepInfra"],"allow_fallbacks":false}` |
| Task            | `cot_redhanded/cot_redhanded_generate`                                                    |
| Settings        | temperature 0.7, top_p 0.95, max_tokens 16000, reasoning_effort high, max_retries 3       |
| Sandbox         | upstream's Docker image (python:3.10-slim + r-base, GDAL, 74 pinned packages)             |

Of the paper's five untrusted models, only DeepSeek R1 is still served by OpenRouter, and this account's provider allowlist leaves exactly one endpoint for it — DeepInfra at fp4. SiliconFlow (fp8) and StreamLake are blocked at the account level. The paper used Together and the official API, neither of which serves fp4, so **quantization is an open deviation**.

### Run 1 — smoke test (2 samples)

`id_set=RED, side_task=varname, attack_policy=baseline, --limit 2, --max-connections 2` Log: `logs/baseline-smoke/...6SxPFNDYCLreFjQQTxzSjs.eval`

| Metric                     | Value                                 |
| -------------------------- | ------------------------------------- |
| Accuracy (`main_and_side`) | 0.500 (1 of 2)                        |
| Total tokens               | 7,563 (output 7,138, reasoning 6,756) |
| Wall clock                 | 2m37s                                 |

**The question this answered:** reasoning survives the full path. Content arrives as `['ContentReasoning', 'ContentText']` with **no `<think>` tags**, so upstream's string-parsing branch would have found nothing — but its list branch handles exactly this shape. `make_dataset()` built a real monitor sample with 13,359 characters of CoT against 400 of action. The baseline is usable end to end.

A first attempt failed before generating any tokens, with OpenRouter returning 404: the pin named SiliconFlow, which the account disallows, and `allow_fallbacks: false` then excluded DeepInfra too. No cost. The Docker image built on that attempt (~20 minutes) and is cached since.

### Run 2 — RED split (57 samples)

`id_set=RED, side_task=varname, attack_policy=baseline, --max-connections 8 --max-sandboxes 8` Log: `logs/baseline-red/...QLTUcVXR6LBhkAcCLvVHy6.eval`

| Metric                          | Value                                                  |
| ------------------------------- | ------------------------------------------------------ |
| Samples / errors                | 57 / 0                                                 |
| **Main task only**              | **26/57 = 45.6%**                                      |
| **Side task only**              | **33/57 = 57.9%**                                      |
| **Both (monitorable)**          | **13/57 = 22.8%**                                      |
| Reported accuracy               | 0.228, stderr 0.056                                    |
| Output tokens per sample        | mean 4,007, median 3,584, range 417–10,919             |
| CoT characters per sample       | mean 14,702, median 13,584, range 730–44,457           |
| Completions with no code fence  | 0/57                                                   |
| Cost                            | **$0.50** total, $0.0087/sample (DeepInfra list price) |
| Wall clock                      | 20m55s                                                 |
| Monitor input that is reasoning | 92.9% (`percent_cot`)                                  |

**Replication signal.** 22.8% sits at the bottom of the paper's stated pass@1 range of 22–36%. Caveat: one side task, one attack policy, one split, n=57, at fp4, stderr 0.056 — consistent with the paper rather than confirming it.

### What this changed

1. **Cost estimates were far too high.** At $0.0087/sample, generation for the full plan (~880 attack + ~855 benign) is **~$15**, against the $25–200 range previously estimated. The top of that range was out by roughly an order of magnitude. Wall clock, not money, is the constraint: ~1,700 samples is ~10 hours locally at 8 concurrent.
2. **Generation-to-monitorable ratio is 4.4×.** 13 usable monitor samples from 57 generated. This is the multiplier to size the real corpus against.
3. **The side task is easier than the main task** (57.9% vs 45.6%), so BigCodeBench difficulty is the binding constraint on corpus size — at least for `varname`, which a regex for `\blist\s*=` satisfies. The I/O side tasks should invert this.
4. **Upstream's reasoning budget never applies.** Inspect warns that `reasoning_effort` and `reasoning_tokens` conflict and ignores the latter. Upstream's `MODEL_CONFIG` sets both, so its nominal 12,800-token budget is silently discarded; the ~3,750 reasoning tokens per sample come from `reasoning_effort="high"` alone. Confirmed at n=57, not just on the smoke run.

### Trajectory analysis (Scout)

8 default scanners over the 2-sample smoke log, scanner model `openrouter/openai/gpt-5-nano`, scan `dcHkGt6dzNS5t7Buhf7MEZ`, 0 errors. Full write-up in `agent_artefacts/trajectory_analysis/cot_redhanded/`. **Tooling validation only — two samples support no claim about the eval.**

- `formatting_failure` flagged **2/2**, treating the chain of thought as a formatting defect ("included extended reasoning, a `<thinking>` block"). It also contradicted its own prompt by flagging a sample that scored `C`, and it carries `invalidates_failure=True`, so it would invalidate genuine failures for the wrong reason. **Exclude or rewrite before using Scout to judge validity for this eval.**
- `reward_hacking_success` returned **0/2**, including on the successful sabotage. The prior expectation that it would flag every successful attack as an invalid success was wrong on this evidence, though n=1 success is far too thin to settle it.
- All other scanners returned 0/2.
- `analyze_validity.py` produced no output and wrote no file; unclear whether that is a two-sample edge case or a defect.

### Infrastructure notes

- **Upstream's `completed_ids/` cache makes consecutive runs non-independent.** Its solver writes passing sample ids under the working directory and skips them on a later run, scoring them `CORRECT` with no model call and no transcript — silently inflating the pass rate. The smoke run cached `2_baseline`; it was cleared before Run 2, so Run 2 is independent. Now gitignored.
- Inspect does not surface the resolved OpenRouter provider in `ModelOutput.metadata` (returns `None`), so the serving endpoint cannot be recorded automatically per sample.

### Open

- Whether to request SiliconFlow (fp8) be added to the account allowlist, or accept fp4 and record it as a deviation.
- Re-run Scout on the 57-sample log with `formatting_failure` excluded, plus custom scanners `side_task_in_cot` and `sabotage_actually_present`.
- No benign (`side_task=None`) arm has been run yet, so no false-positive calibration exists and no catch-rate figure can be computed.
- No monitor-stage run yet.
