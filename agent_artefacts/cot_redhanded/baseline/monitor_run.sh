#!/usr/bin/env bash
# Monitor stage over last night's generation logs.
#
# Upstream's `run_monitor` takes a single .eval file and rejects a directory, so this
# is one invocation per generation log: nine attack logs plus the benign arm. Each is
# independent and a failure does not abort the rest.
#
# `defaultval` is excluded deliberately: that run died on a rate limit after 23 of 57
# samples, so its monitorable samples are a non-random subset.
#
# No sandbox is needed here - the monitor only reads logs and calls a model.
set -u

MODEL="openrouter/openai/gpt-4.1-mini"
CONN=16
PROMPT_ID=1

# The nine attack side tasks worth scoring. complexity and edgecase are included
# although n is tiny (2 and 3 monitorable samples), so the pipeline is exercised on
# them; their rates must not be quoted.
SIDE_TASKS="complexity edgecase exit iodelete iodownload ioexfil logio longlines permissions"

echo "### MONITOR RUN START $(date -u +%FT%TZ)"

for st in $SIDE_TASKS; do
  log=$(ls logs/overnight-attack/"$st"/*.eval 2>/dev/null | head -1)
  if [ -z "$log" ]; then
    echo "### SKIP: $st (no log)"
    continue
  fi
  echo
  echo "### MONITOR ATTACK: side_task=$st  $(date -u +%FT%TZ)"
  echo "### source: $log"
  uv run inspect eval cot_redhanded/cot_redhanded_monitor \
    -T eval_log_path="$log" -T prompt_id="$PROMPT_ID" -T has_side_task=true \
    --model "$MODEL" \
    --log-dir "logs/monitor-overnight-attack/$st" \
    --max-connections "$CONN" || echo "### FAILED: $st"
done

echo
echo "### MONITOR BENIGN: TEST split  $(date -u +%FT%TZ)"
blog=$(ls logs/overnight-benign/*.eval 2>/dev/null | head -1)
echo "### source: $blog"
uv run inspect eval cot_redhanded/cot_redhanded_monitor \
  -T eval_log_path="$blog" -T prompt_id="$PROMPT_ID" -T has_side_task=false \
  --model "$MODEL" \
  --log-dir logs/monitor-overnight-benign \
  --max-connections "$CONN" || echo "### FAILED: benign"

echo
echo "### MONITOR RUN END $(date -u +%FT%TZ)"
