"""Compare the native dataset with the reference implementation's, sample by sample.

This was run while the reference package was still a dependency, and passed. To run it
again, install the reference package first:

    uv pip install git+https://github.com/cotredhanded/code@505d9fca6fc5b859492d8b9500b374c8cbf501e8

    uv run python agent_artefacts/cot_redhanded/parity/dataset_parity.py

It builds every (side task, attack policy, split) both ways, fails loudly on any difference,
and writes `tests/cot_redhanded/fixtures/dataset_parity.json`: a digest of each combination
that the test suite checks the native dataset against once the reference package is gone.
"""

import hashlib
import json
import sys
from pathlib import Path

from cot_red_handed.tasks.base import load_red_dataset

from cot_redhanded.dataset import ID_SETS, load_cot_redhanded_dataset
from cot_redhanded.side_tasks import ATTACK_POLICIES, SIDE_TASKS

FIXTURE = Path("tests/cot_redhanded/fixtures/dataset_parity.json")
# Keys the two implementations name differently: ours -> upstream's.
SHARED_METADATA = {
    "problem": "prompt",
    "reference_solution": "code",
    "canonical_solution_length": "canonical_solution_length",
    "input_types": "input_types",
    "output_types": "output_types",
    "side_test_case": "side_test_case",
    "benchmark_time": "benchmark_time",
    "idea": "idea",
    "obviousness_rating": "obviousness_rating",
    "sabotaged_code": "sabotaged_code",
}


def canonical(task_id, sample, upstream: bool) -> dict:
    metadata = sample.metadata or {}
    text = sample.input
    if upstream:
        # Upstream fills this placeholder later, in its solver; natively the dataset does it.
        text = text.replace("{side_test_case}", metadata.get("side_test_case", ""))
    record = {
        "task_id": task_id,
        "input": text,
        "target": sample.target,
        "files": sample.files or {},
    }
    for ours, theirs in SHARED_METADATA.items():
        record[ours] = metadata.get(theirs if upstream else ours)
    return record


def digest(records: list[dict]) -> str:
    blob = json.dumps(records, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()


def main() -> int:
    fixture: dict[str, dict] = {}
    failures = 0
    combos = [(None, None)] + [(s, p) for s in SIDE_TASKS for p in ATTACK_POLICIES]
    for side_task, policy in combos:
        for id_set in ID_SETS:
            for use_pairings in (False, True):
                if side_task is None and use_pairings:
                    continue
                theirs = load_red_dataset(
                    "bigcodebench",
                    side_task=side_task or "baseline",
                    attack_policy=policy or "baseline",
                    id_set=id_set,
                    use_pairings=use_pairings,
                )
                ours = load_cot_redhanded_dataset(
                    [side_task] if side_task else [],
                    [policy] if policy else [],
                    id_set,
                    use_pairings=use_pairings,
                    include_honest=side_task is None,
                )
                theirs_records = [canonical(s.id, s, True) for s in theirs]
                ours_records = [canonical(s.metadata["task_id"], s, False) for s in ours]
                key = f"{side_task or 'honest'}/{policy or '-'}/{id_set}/{'paired' if use_pairings else 'all'}"
                if theirs_records != ours_records:
                    failures += 1
                    print(
                        f"MISMATCH {key}: {len(theirs_records)} upstream vs {len(ours_records)} native"
                    )
                    for a, b in zip(theirs_records, ours_records):
                        if a != b:
                            print(
                                "  first differing sample",
                                a["task_id"],
                                [k for k in a if a[k] != b.get(k)],
                            )
                            break
                fixture[key] = {"n": len(ours_records), "sha256": digest(ours_records)}
        print(f"checked {side_task or 'honest'} / {policy or '-'}", flush=True)
    if failures:
        print(f"{failures} combinations differ; fixture not written")
        return 1
    FIXTURE.write_text(json.dumps(fixture, indent=1, sort_keys=True) + "\n")
    print(f"all {len(fixture)} combinations identical; wrote {FIXTURE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
