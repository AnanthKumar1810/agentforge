# AgentForge competition demo

## One-minute explanation

AgentForge takes a repair request, copies the source into a run workspace, gives
an agent guarded file tools, runs maintained tests in a bounded validator, and
saves the diff and check output. A failed check feeds a bounded repair round.
The deterministic validator decides the status; the model cannot mark its own
work as verified.

## Live demonstration

From the repository root, with the development environment installed:

```bash
python -m examples.demo_repair
```

Show the printed sequence: the first validation fails, the repair round passes,
and the original fixture still returns `0`. The command writes a readable report
at `.agentforge-data/demo-report.md`. This demo uses scripted agent replies so
it is quick and reliable on a stage; identify it as a workflow demonstration,
not a live model-quality result.

For the real-model evidence, open the
[November 1 benchmark run](https://github.com/AnanthKumar1810/agentforge/actions/runs/41267363640)
and its `ollama-repair-*` artifacts. The Iniconfig case shows a source-only diff
and a verified full suite. Four other cases made no accepted edit.

## Accurate evidence statement

> We tested the benchmark setup against five pinned, independently maintained
> Python repositories. Each upstream suite passed, and each injected regression
> failed as expected. With a local 3B Ollama model, one guided repair passed its
> full suite in Docker. We are improving the edit workflow and expanding the
> benchmark to larger repositories with measured repeatability.

The [benchmark record](REPOSITORY_BENCHMARK.md) lists commits and outcomes.
Do not describe the five preflight successes as five model repairs. The repair
prompts contain the expected original line, so this result measures guided tool
use and validation, not autonomous diagnosis. More runs and larger repositories
are needed before claiming a reliable general repair rate.
