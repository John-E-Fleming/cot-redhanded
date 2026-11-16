# CoT Red-Handed: Stress Testing Chain-of-Thought Monitoring

Stress tests chain-of-thought monitoring as an AI control measure. A weaker trusted monitor reads a
stronger untrusted model's reasoning trace and final code to detect sabotage hidden alongside a
legitimate coding task, and is compared against an action-only monitor that sees the code alone and a
hybrid protocol that scores reasoning and actions independently before combining them. Monitors are
compared by catch rate at a 1% false positive rate, calibrated against benign completions.

Based on [CoT Red-Handed: Stress Testing Chain-of-Thought Monitoring](https://arxiv.org/abs/2505.23575)
(Arnav et al., NeurIPS 2025), with the [reference implementation](https://github.com/cotredhanded/code)
as the comparison point.

<!-- Contributors: Automatically Generated -->
Contributed by [@John-E-Fleming](https://github.com/John-E-Fleming)
<!-- /Contributors: Automatically Generated -->

<!-- Usage: Automatically Generated -->
## Usage

First, install dependencies:

```bash
uv sync
```

Then run evaluations:

```bash
uv run inspect eval cot_redhanded/cot_redhanded --model openai/gpt-5-nano
```

You can also import tasks as Python objects:

```python
from inspect_ai import eval
from cot_redhanded import cot_redhanded
eval(cot_redhanded)
```

If your directory name doesn't match your package name but you have registered your eval task, you can also run:

```bash
uv run inspect eval cot_redhanded --model openai/gpt-5-nano
```

After running evaluations, view logs with:

```bash
uv run inspect view
```

If you don't want to specify `--model` each time, create a `.env` file:

```bash
INSPECT_EVAL_MODEL=anthropic/claude-opus-4-1-20250805
ANTHROPIC_API_KEY=<anthropic-api-key>
```
<!-- /Usage: Automatically Generated -->

<!-- Options: Automatically Generated -->
## Options

You can control a variety of options from the command line. For example:

```bash
uv run inspect eval cot_redhanded/cot_redhanded --limit 10
uv run inspect eval cot_redhanded/cot_redhanded --max-connections 10
uv run inspect eval cot_redhanded/cot_redhanded --temperature 0.5
```

See `uv run inspect eval --help` for all available options.
<!-- /Options: Automatically Generated -->

<!-- Parameters: Automatically Generated -->
## Parameters

### `cot_redhanded`

No task parameters.
<!-- /Parameters: Automatically Generated -->

## Dataset

Describe the dataset here: where it comes from, how many samples it has, and how a record becomes a `Sample`.

## Scoring

Describe the scorer here: what counts as correct, and which metrics the task reports.

## Deviations from the original implementation

| What changed | Why | Expected effect on scores |
| --- | --- | --- |
| Nothing yet. | | |

## Architecture

Add a diagram of the task here: dataset, solver, scorer, and any sandbox or judge model.
