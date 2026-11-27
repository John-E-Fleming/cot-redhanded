#!/usr/bin/env bash
# Overnight baseline: the ten side tasks not yet run, then a large benign arm.
#
# Each task is run independently and failures do not abort the rest, so one broken
# side task cannot cost the whole night. Upstream writes a completed_ids file per
# (model, side_task, attack_policy), so the runs do not interfere with each other;
# the cache is cleared once before starting.
#
# Resumable: re-running skips nothing automatically, so delete the log dir for a
# task before re-running it.
set -u

MODEL="openrouter/deepseek/deepseek-r1-0528"
PROVIDER='{"order":["DeepInfra"],"allow_fallbacks":false}'
CONN=12
SANDBOXES=8

# varname already done; these are the remaining ten.
SIDE_TASKS="complexity defaultval edgecase exit iodelete iodownload ioexfil logio longlines permissions"

echo "### OVERNIGHT RUN START $(date -u +%FT%TZ)"

for st in $SIDE_TASKS; do
  echo
  echo "### ATTACK: side_task=$st  $(date -u +%FT%TZ)"
  uv run inspect eval cot_redhanded/cot_redhanded_generate \
    -T id_set=RED -T side_task="$st" -T attack_policy=baseline \
    --model "$MODEL" -M provider="$PROVIDER" \
    --log-dir "logs/overnight-attack/$st" \
    --max-connections "$CONN" --max-sandboxes "$SANDBOXES" || echo "### FAILED: $st"
done

echo
echo "### BENIGN: TEST split, 855 samples  $(date -u +%FT%TZ)"
uv run inspect eval cot_redhanded/cot_redhanded_generate \
  -T id_set=TEST -T side_task=baseline -T attack_policy=baseline \
  --model "$MODEL" -M provider="$PROVIDER" \
  --log-dir logs/overnight-benign \
  --max-connections "$CONN" --max-sandboxes "$SANDBOXES" || echo "### FAILED: benign"

echo
echo "### OVERNIGHT RUN END $(date -u +%FT%TZ)"
