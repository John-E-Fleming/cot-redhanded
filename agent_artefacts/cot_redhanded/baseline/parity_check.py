"""Check that reaching upstream through this wrapper matches upstream's own runner.

The wrapper exists so a baseline can be run with `inspect eval`. Upstream is normally driven by
`cot_red_handed.tasks.run_tasks`, which configures things the Task object does not carry, so
the two entry points can diverge without anything appearing to be wrong. This compares them
field by field.

Run:  uv run python agent_artefacts/cot_redhanded/baseline/parity_check.py
"""

import sys

from cot_red_handed.tasks.config import EPOCH_METRICS, MODEL_CONFIG, NUM_EPOCHS, SANDBOX
from cot_red_handed.tasks.tasks import TaskManager

from cot_redhanded import cot_redhanded_generate

SIDE_TASK = "varname"
ATTACK_POLICY = "baseline"
ID_SET = "RED"  # the smallest split, 57 ids, so the check is quick


def main() -> int:
    # What upstream's own runner would build.
    upstream = TaskManager().generate_task(
        main_task_name="bigcodebench",
        side_task_name=SIDE_TASK,
        attack_policies=[ATTACK_POLICY],
        id_set=ID_SET,
    )
    # What `inspect eval` gets through this repository.
    wrapped = cot_redhanded_generate(
        side_task=SIDE_TASK, attack_policy=ATTACK_POLICY, id_set=ID_SET
    )

    failures: list[str] = []

    def check(label: str, got: object, want: object) -> None:
        status = "ok " if got == want else "FAIL"
        if got != want:
            failures.append(f"{label}: wrapper={got!r} upstream={want!r}")
        print(f"  [{status}] {label:24} {got!r}")

    print("Task shape")
    check("name", wrapped.name, upstream.name)
    check("dataset size", len(wrapped.dataset), len(upstream.dataset))
    check("sample ids", [s.id for s in wrapped.dataset], [s.id for s in upstream.dataset])
    check("scorer count", len(wrapped.scorer or []), len(upstream.scorer or []))
    check(
        "sandbox",
        (wrapped.sandbox.type, bool(wrapped.sandbox.config)) if wrapped.sandbox else None,
        (SANDBOX, True),
    )
    check("epochs", wrapped.epochs, NUM_EPOCHS)
    check("epochs (vs upstream)", wrapped.epochs, upstream.epochs)
    # Inspect normalises Epochs() into an int plus a list of reducer callables.
    check(
        "epoch reducer count",
        len(wrapped.epochs_reducer or []),
        len(EPOCH_METRICS),
    )
    check(
        "epoch reducers (vs upstream)",
        len(wrapped.epochs_reducer or []),
        len(upstream.epochs_reducer or []),
    )

    # The settings upstream puts on the model rather than the task. These are the ones that
    # silently go missing when upstream is reached through `inspect eval`.
    print("\nGeneration settings (upstream applies these via ModelManager)")
    model_cfg = MODEL_CONFIG.model_dump(exclude_none=True)
    wrapped_cfg = wrapped.config.model_dump(exclude_none=True)
    for field, want in sorted(model_cfg.items()):
        check(field, wrapped_cfg.get(field), want)

    print("\nSample content")
    w0, u0 = wrapped.dataset[0], upstream.dataset[0]
    check("first sample id", w0.id, u0.id)
    check("first sample input", str(w0.input), str(u0.input))
    check("first sample target", str(w0.target), str(u0.target))
    check("metadata keys", sorted((w0.metadata or {}).keys()), sorted((u0.metadata or {}).keys()))

    print()
    if failures:
        print(f"PARITY FAILED: {len(failures)} difference(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PARITY OK: the wrapper and upstream's own runner agree on every checked field.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
