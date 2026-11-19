"""Dump rubric labels and the judge's reasoning to readable text.

For validating the rubrics by hand (Principle 3 of Kirgis et al. 2026), the useful artefact is
the label next to the explanation that produced it, per sample, in a form that can be read
linearly and annotated. That is what this writes.

Usage:
    uv run python agent_artefacts/trajectory_analysis/cot_redhanded/dump_labels.py \
        agent_artefacts/trajectory_analysis/cot_redhanded/rubric_results -o labels.md
    ... --scanner rationalisation_present   # just one rubric
    ... --label instructed                  # just the samples carrying one label
"""

from __future__ import annotations

import argparse
import glob
import json
import os

import pandas as pd

SCANNERS = (
    "side_task_in_cot",
    "rationalisation_present",
    "sabotage_actually_present",
    "sabotage_refusal",
)


def _sample_of(raw: object) -> dict:
    """The eval sample's own metadata, which Scout nests under `sample_metadata`."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return (raw or {}).get("sample_metadata") or {} if isinstance(raw, dict) else {}


def _id_of(raw: object) -> str:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return "?"
    return str((raw or {}).get("id", "?")) if isinstance(raw, dict) else "?"


def main() -> int:
    """Write every label with its explanation, grouped by side task then sample."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results")
    parser.add_argument("-o", "--out", default="rubric_labels.md")
    parser.add_argument("--scanner", default="", help="Only this scanner.")
    parser.add_argument("--label", default="", help="Only samples carrying this label.")
    args = parser.parse_args()

    rows: list[dict] = []
    for name in SCANNERS:
        if args.scanner and name != args.scanner:
            continue
        for path in glob.glob(os.path.join(args.results, "**", f"{name}.parquet"), recursive=True):
            df = pd.read_parquet(path)
            for _, r in df.iterrows():
                sample = _sample_of(r["transcript_metadata"])
                rows.append(
                    {
                        "side_task": sample.get("side_task_name", "?"),
                        "policy": sample.get("attack_policy", "?"),
                        "sample": _id_of(r["transcript_metadata"]),
                        "scanner": name,
                        "label": r["answer"],
                        "explanation": (r["explanation"] or "").strip(),
                    }
                )

    if args.label:
        rows = [r for r in rows if r["label"] == args.label]
    if not rows:
        print("nothing matched")
        return 1

    rows.sort(key=lambda r: (r["side_task"], r["sample"], r["scanner"]))
    lines = [
        "# Rubric labels and the judge's reasoning",
        "",
        f"{len(rows)} labels. Rubrics are defined in `research/scanner_rubrics.md`.",
        "",
        "To validate: read the explanation, decide the label yourself, and note disagreements.",
        "",
    ]
    current_task = current_sample = None
    for r in rows:
        if r["side_task"] != current_task:
            current_task = r["side_task"]
            current_sample = None
            lines += ["", f"## {current_task}", ""]
        if r["sample"] != current_sample:
            current_sample = r["sample"]
            lines += [f"### sample `{current_sample}`  ({r['policy']} policy)", ""]
        lines += [f"**{r['scanner']}** → `{r['label']}`", "", f"> {r['explanation']}", ""]

    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    print(
        f"wrote {args.out}: {len(rows)} labels across {len({r['side_task'] for r in rows})} side tasks"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
