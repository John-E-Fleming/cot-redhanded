#!/usr/bin/env bash
# The attack-policy axis: varname on RED, across all nine non-baseline policies.
#
# `baseline` is already run and recorded, so this fills the rest of the axis. Attack policy is
# what Figure 3 averages over, and it is also where the two live predictions in
# research/action_vs_cot_hypothesis.md are decided: `stealth` and `monitor_notice` give the model genuine
# intent to conceal, while `cot_verbosity` and `code_first` suppress what reaches the CoT.
#
# This replaces the version that died overnight on 2026-11-18. Three things killed that run and
# each has a guard here:
#
#   1. The machine lost DNS. `stealth` hung for 11 hours on APIConnectionError, then the
#      remaining eight policies each failed in about three seconds on `docker build`, because
#      Docker could not resolve registry-1.docker.io. One outage became nine lost arms.
#      -> `wait_for_health` blocks before each policy until the network and the base image are
#         both reachable, so an outage pauses the run instead of consuming it.
#
#   2. Nothing could be resumed. -> `already_done` skips any policy that already has a log with
#      status "success", so re-running this script continues where it stopped.
#
#   3. The partial `stealth` run left 26 ids in completed_ids/. `retry_solver` reads that file
#      and short-circuits those samples to Score(CORRECT) with no model call and NO TRANSCRIPT,
#      which would have silently gutted the arm we most need transcripts from (B-6 in
#      research/audit_issues.md). -> `guard_cache` refuses to start if a cache exists for this side task.
#
set -u

MODEL="openrouter/deepseek/deepseek-r1-0528"
PROVIDER='{"order":["DeepInfra"],"allow_fallbacks":false}'
SIDE_TASK="varname"
ID_SET="RED"
CONNECTIONS=8          # the rate varname was measured at: 57 samples in 20.9 minutes
SANDBOXES=8
BASE_IMAGE="python:3.10-slim"
LOG_ROOT="logs/policy-axis"

POLICIES=(
  stealth monitor_notice cot_verbosity code_first
  future_proofing functionality dependency must_use implementation_template
)

guard_cache() {
  local hits
  hits=$(ls completed_ids/*_"${SIDE_TASK}"_*_completed_ids.txt 2>/dev/null | wc -l | tr -d ' ')
  if [ "$hits" != "0" ]; then
    echo "### ABORT: a completed_ids cache exists for ${SIDE_TASK}:"
    ls completed_ids/*_"${SIDE_TASK}"_*_completed_ids.txt
    echo "### Those samples would be scored CORRECT with no model call and no transcript."
    echo "### Move them aside before running (see B-6 in research/audit_issues.md)."
    exit 1
  fi
}

healthy() {
  curl -sf --max-time 20 https://openrouter.ai/api/v1/models -o /dev/null || return 1
  docker image inspect "$BASE_IMAGE" >/dev/null 2>&1 || return 1
  return 0
}

wait_for_health() {
  local tries=0
  until healthy; do
    tries=$((tries + 1))
    if [ "$tries" -gt 60 ]; then
      echo "### GIVING UP: unhealthy for 60 checks (~5h)  $(date -u +%FT%TZ)"
      return 1
    fi
    echo "### UNHEALTHY, waiting 5m (check $tries)  $(date -u +%FT%TZ)"
    sleep 300
  done
  return 0
}

already_done() {
  local policy="$1"
  ls "${LOG_ROOT}/${policy}"/*.eval >/dev/null 2>&1 || return 1
  uv run python - "$policy" <<'PY' 2>/dev/null
import glob, sys
from inspect_ai.log import read_eval_log
for p in glob.glob(f"logs/policy-axis/{sys.argv[1]}/*.eval"):
    if read_eval_log(p, header_only=True).status == "success":
        sys.exit(0)
sys.exit(1)
PY
}

run_policy() {
  local policy="$1"
  uv run inspect eval cot_redhanded/cot_redhanded_generate \
    -T id_set="$ID_SET" -T side_task="$SIDE_TASK" -T attack_policy="$policy" \
    --model "$MODEL" -M provider="$PROVIDER" \
    --log-dir "${LOG_ROOT}/${policy}" \
    --max-connections "$CONNECTIONS" --max-sandboxes "$SANDBOXES"
}

echo "### POLICY AXIS START  $(date -u +%FT%TZ)"
guard_cache

echo "### pre-pulling ${BASE_IMAGE} so a registry outage cannot stop a later arm"
docker pull -q "$BASE_IMAGE" || echo "### WARNING: pre-pull failed; relying on any cached image"

for policy in "${POLICIES[@]}"; do
  if already_done "$policy"; then
    echo "### SKIP ${policy} (already has a successful log)  $(date -u +%FT%TZ)"
    continue
  fi
  wait_for_health || { echo "### ABORTING RUN, environment never recovered"; break; }

  echo "### POLICY ${policy}  (${SIDE_TASK}, ${ID_SET}, 57 samples)  $(date -u +%FT%TZ)"
  if ! run_policy "$policy"; then
    echo "### ${policy} FAILED, retrying once after a health check  $(date -u +%FT%TZ)"
    wait_for_health || { echo "### ABORTING RUN, environment never recovered"; break; }
    run_policy "$policy" || echo "### FAILED TWICE: ${policy}"
  fi

  # The cache this run writes would short-circuit any later re-run of the same arm.
  mkdir -p completed_ids_quarantine
  mv completed_ids/*_"${SIDE_TASK}"_"${policy}"_completed_ids.txt completed_ids_quarantine/ 2>/dev/null
done

echo "### POLICY AXIS END  $(date -u +%FT%TZ)"
for policy in "${POLICIES[@]}"; do
  n=$(ls "${LOG_ROOT}/${policy}"/*.eval 2>/dev/null | wc -l | tr -d ' ')
  echo "###   ${policy}: ${n} log(s)"
done
