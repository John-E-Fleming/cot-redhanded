"""Cross-tabulate rubric labels against verifier outcomes.

Principle 4 of Kirgis et al. (2026): "Compute prevalence by outcome first." A label count on its
own says nothing. The label crossed with the verifier's verdict is the measurement, and for R3 the
disagreement between them *is* the finding.

Usage:
    uv run python agent_artefacts/trajectory_analysis/cot_redhanded/crosstab.py <results_dir> ...
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

#: Label orders, so tables read from "nothing there" to "definitely there".
ORDERS = {
    "side_task_in_cot": ["none", "oblique", "procedural", "substantive"],
    "rationalisation_present": ["absent", "instructed", "present-weak", "present-strong"],
    "sabotage_actually_present": ["absent", "partial", "present", "cannot_determine"],
    "sabotage_refusal": ["none", "soft-omission", "hedged", "explicit"],
}


def _meta(raw: object) -> dict:
    """Sample metadata, which parquet may hold as a JSON string."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}


def load(results_dirs: list[str]) -> pd.DataFrame:
    """One row per transcript, one column per scanner, plus the verifier outcome."""
    frames = []
    for d in results_dirs:
        per_scanner = {}
        for name in SCANNERS:
            paths = glob.glob(os.path.join(d, "**", f"{name}.parquet"), recursive=True)
            if not paths:
                continue
            df = pd.read_parquet(paths[0])
            per_scanner[name] = df.set_index("transcript_id")["answer"]
            if "meta" not in per_scanner:
                meta = df.set_index("transcript_id")["transcript_metadata"].map(_meta)
                per_scanner["meta"] = meta
        if not per_scanner:
            continue
        out = pd.DataFrame({k: v for k, v in per_scanner.items() if k != "meta"})
        meta = per_scanner["meta"]
        # Scout nests the eval sample under `sample_metadata`, and the verifier's own breakdown
        # under `scores.<scorer>.metadata`. The top level holds only Scout's own fields, so
        # reading `side_task_name` from there silently yields "?" for every row.
        sample = meta.map(lambda m: m.get("sample_metadata") or {})
        out["sample_id"] = meta.map(lambda m: m.get("id", "?"))
        out["side_task"] = sample.map(lambda s: s.get("side_task_name", "?"))
        out["attack_policy"] = sample.map(lambda s: s.get("attack_policy", "?"))

        def _verifier(m: dict) -> dict:
            for entry in (m.get("scores") or {}).values():
                if isinstance(entry, dict) and entry.get("metadata"):
                    return entry["metadata"]
            return {}

        verifier = meta.map(_verifier)
        out["v_main"] = verifier.map(lambda v: bool(v.get("main_only")))
        out["v_side"] = verifier.map(lambda v: bool(v.get("side_only")))
        frames.append(out)
    return pd.concat(frames) if frames else pd.DataFrame()


def main() -> int:
    """Print prevalence and the cross-tabs that matter."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", nargs="+")
    args = parser.parse_args()

    df = load(args.results)
    if df.empty:
        print("no results found")
        return 1

    print(f"transcripts: {len(df)}")
    print(f"side tasks : {sorted(df['side_task'].unique())}")
    print()

    print("=" * 76)
    print("PREVALENCE (Principle 4: by outcome first)")
    print("=" * 76)
    for name in SCANNERS:
        if name not in df:
            continue
        print(f"\n{name}")
        order = [x for x in ORDERS[name] if x in set(df[name])]
        extra = [x for x in df[name].dropna().unique() if x not in order]
        tab = pd.crosstab(df["side_task"], df[name]).reindex(columns=order + extra, fill_value=0)
        print(tab.to_string())

    print()
    print("=" * 76)
    print("R3 vs THE VERIFIER  (the audit measurement)")
    print("=" * 76)
    if "sabotage_actually_present" in df:
        for task, grp in df.groupby("side_task"):
            tab = pd.crosstab(grp["v_side"], grp["sabotage_actually_present"])
            print(f"\n{task}:  rows = verifier side_only, cols = sabotage in code")
            print(tab.to_string())
            disagree = grp[
                grp["v_side"] & grp["sabotage_actually_present"].isin(["absent", "partial"])
            ]
            if len(disagree):
                print(
                    f"  --> {len(disagree)}/{int(grp['v_side'].sum())} verifier side-task passes "
                    "have NO or PARTIAL sabotage in the code"
                )

    print()
    print("=" * 76)
    print("R1 x R3  (planned it vs did it)")
    print("=" * 76)
    if "side_task_in_cot" in df and "sabotage_actually_present" in df:
        print(pd.crosstab(df["side_task_in_cot"], df["sabotage_actually_present"]).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
