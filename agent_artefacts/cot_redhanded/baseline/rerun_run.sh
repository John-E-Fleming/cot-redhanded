#!/usr/bin/env bash
# Fills the two gaps the overnight run left.
#
# 1. defaultval on RED, re-run. The overnight attempt died on an OpenRouter rate
#    limit after tenacity exhausted upstream's max_retries=3, leaving a non-random
#    subset of 23 scored samples. Concurrency is halved here. That is a harness
#    knob, not an eval parameter, so parity with upstream is unaffected.
#
#    Its completed_ids cache was moved aside first. Upstream reads that file and
#    short-circuits a cached sample to Score(CORRECT) with no model call and no
#    transcript, so without the move 6 of 57 samples would have inherited free
#    passes from the failed run.
#
# 2. complexity and edgecase on TEST. Both are restricted to upstream's annotated
#    ids, of which only 7 each fall in RED - too few to monitor (they yielded 2 and
#    3 monitorable samples). TEST holds 113 of each. These are the two side tasks
#    where the paper's CoT advantage should be clearest, so they are the runs that
#    matter most for the inverted pooled result.
#
# RED and TEST are disjoint, so the complexity and edgecase caches cannot cause a
# skip here; this was checked before launching rather than assumed.
#
# Logs go to logs/rerun/ to keep them distinct from the overnight dirs, which still
# hold the failed defaultval log and the 7-sample RED runs.
set -u

MODEL="openrouter/deepseek/deepseek-r1-0528"
PROVIDER='{"order":["DeepInfra"],"allow_fallbacks":false}'
SANDBOXES=8

echo "### RERUN START $(date -u +%FT%TZ)"

echo
echo "### defaultval RED, 57 samples, conn=6  $(date -u +%FT%TZ)"
uv run inspect eval cot_redhanded/cot_redhanded_generate \
  -T id_set=RED -T side_task=defaultval -T attack_policy=baseline \
  --model "$MODEL" -M provider="$PROVIDER" \
  --log-dir logs/rerun/defaultval \
  --max-connections 6 --max-sandboxes "$SANDBOXES" || echo "### FAILED: defaultval"

for st in complexity edgecase; do
  echo
  echo "### $st TEST, 113 samples, conn=12  $(date -u +%FT%TZ)"
  uv run inspect eval cot_redhanded/cot_redhanded_generate \
    -T id_set=TEST -T side_task="$st" -T attack_policy=baseline \
    --model "$MODEL" -M provider="$PROVIDER" \
    --log-dir "logs/rerun/$st-test" \
    --max-connections 12 --max-sandboxes "$SANDBOXES" || echo "### FAILED: $st"
done

echo
echo "### RERUN END $(date -u +%FT%TZ)"
