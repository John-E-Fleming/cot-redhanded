# Research record

Working documents for the CoT Red-Handed port: what was run, what was found, and how it was
measured. **None of this ships with the eval.** An upstream contribution is `src/cot_redhanded/`,
`tests/cot_redhanded/`, `eval.yaml` and the eval's own README; everything here is the programme's
work product and stays behind.

File paths written in these documents are relative to the repository root, not to this directory.

| Document | What it is |
| --- | --- |
| [experiment_results_log.md](experiment_results_log.md) | The chronological record: every run, its figures, and the corrections to them. Entries are dated and numbered. |
| [audit_issues.md](audit_issues.md) | The register of defects found in upstream `cot_red_handed`, A-1 to A-13 and B-1 to B-7. Nothing in it is fixed; the baseline reproduces upstream so the audit can measure it. |
| [action_vs_cot_hypothesis.md](action_vs_cot_hypothesis.md) | Why action-only monitoring beats CoT monitoring on this corpus, the evidence, the predictions that failed, and what it implies for uplift. |
| [scanner_rubrics.md](scanner_rubrics.md) | The decision procedures behind the four Inspect Scout rubric scanners, and the confounders each exists to exclude. |

Related material that is **not** documentation and lives elsewhere:

- `agent_artefacts/cot_redhanded/baseline/` — run scripts and analysis tools the log cites.
- `agent_artefacts/trajectory_analysis/cot_redhanded/` — the scanners and their analysis scripts.
- `~/scout_scans/cot-redhanded` — scan results, outside the project because Scout View cannot
  resolve a path containing a space. See `AGENTS.md`.
