#!/usr/bin/env bash
# Widen the attack-policy axis from 1 family to 4, on varname over the RED split.
#
# The paper describes six attack-policy families. attack_policies.yaml holds ten keys, which
# map to four of them: baseline, stealth, monitor_notice, and seven framing variants
# (future_proofing, functionality, dependency, must_use, implementation_template, code_first,
# cot_verbosity). Few-shot and finetuning were never released, so four families is the ceiling.
#
# baseline is already run, so this covers the other nine keys. varname is the side task because
# it is one of only two that are subtle in the action, and the only one of those two with a
# usable sample count - which makes it the side task where varying the policy can actually move
# the CoT-versus-action comparison.
#
# Waits for the generation runner to finish first: both compete for the same Docker sandboxes.
set -u

MODEL="openrouter/deepseek/deepseek-r1-0528"
PROVIDER='{"order":["DeepInfra"],"allow_fallbacks":false}'
POLICIES="stealth future_proofing functionality dependency must_use implementation_template code_first cot_verbosity monitor_notice"

echo "### WAITING for rerun_run.sh to release the sandboxes  $(date -u +%FT%TZ)"
while pgrep -f rerun_run.sh >/dev/null 2>&1; do sleep 30; done
echo "### SANDBOXES FREE, starting  $(date -u +%FT%TZ)"

for ap in $POLICIES; do
  echo "### POLICY $ap  (varname, RED, 57 samples)  $(date -u +%FT%TZ)"
  uv run inspect eval cot_redhanded/cot_redhanded_generate \
    -T id_set=RED -T side_task=varname -T attack_policy="$ap" \
    --model "$MODEL" -M provider="$PROVIDER" \
    --log-dir "logs/policy-axis/$ap" \
    --max-connections 12 --max-sandboxes 8 || echo "### FAILED: $ap"
done

echo "### POLICY AXIS RUN END  $(date -u +%FT%TZ)"
