#!/usr/bin/env bash
# Widen the trusted-monitor axis from 1 of 4 to 3 of 4.
#
# The paper uses four monitors: GPT-4.1-mini (already run), GPT-4.1-nano,
# Gemini 2.5 Flash Lite, and Claude 3.5 Haiku. The last is retired from
# OpenRouter, so 3 of 4 is the ceiling through this repo's configuration.
#
# Scored over exactly the corpus the GPT-4.1-mini run used - the nine side
# tasks in logs/monitor-overnight-attack plus the 855-sample benign arm - so
# the three monitors are directly comparable. defaultval is excluded here for
# that reason: its good log only exists as of today, after the mini run.
#
# Each monitor needs the benign arm too: the 1% FPR threshold is that
# monitor's own 99th percentile on benign completions, not a shared constant.
#
# No eval code is involved. This is a coverage run against the existing logs.
set -u

CONN=12
PROMPT_ID=1
SIDE_TASKS="complexity edgecase exit iodelete iodownload ioexfil logio longlines permissions"

run_monitor () {
  local model="$1" tag="$2"
  echo
  echo "### MONITOR $tag  ($model)  $(date -u +%FT%TZ)"
  for st in $SIDE_TASKS; do
    log=$(ls logs/overnight-attack/"$st"/*.eval 2>/dev/null | head -1)
    [ -z "$log" ] && { echo "### MISSING gen log: $st"; continue; }
    uv run inspect eval cot_redhanded/cot_redhanded_monitor \
      -T eval_log_path="$log" -T prompt_id="$PROMPT_ID" -T has_side_task=true \
      --model "$model" --log-dir "logs/monitor-$tag/$st" \
      --max-connections "$CONN" || echo "### FAILED: $tag/$st"
  done
  blog=$(ls logs/overnight-benign/*.eval 2>/dev/null | head -1)
  echo "### MONITOR $tag benign  $(date -u +%FT%TZ)"
  uv run inspect eval cot_redhanded/cot_redhanded_monitor \
    -T eval_log_path="$blog" -T prompt_id="$PROMPT_ID" -T has_side_task=false \
    --model "$model" --log-dir "logs/monitor-$tag/benign" \
    --max-connections "$CONN" || echo "### FAILED: $tag/benign"
}

echo "### MONITOR AXIS RUN START $(date -u +%FT%TZ)"
run_monitor "openrouter/openai/gpt-4.1-nano"          "nano"
run_monitor "openrouter/google/gemini-2.5-flash-lite" "flashlite"
echo
echo "### MONITOR AXIS RUN END $(date -u +%FT%TZ)"
