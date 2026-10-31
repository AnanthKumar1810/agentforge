# Pinned repository repair benchmark

The benchmark downloads two public repositories at exact commits, imports each into
disposable workspaces, and changes one production line. It confirms the upstream
test suite passes and the mutation fails before asking the local model to repair
the regression. Source repositories and their tests are never edited. Only the
Docker validator executes downloaded code: network is disabled, the workspace is
read-only inside the container, privileges are dropped, and CPU, memory, process,
and time limits apply. The GitHub runners are disposable.

| Case | Pinned upstream commit | Injected regression | Upstream suite | Mutant suite |
| --- | --- | --- | --- | --- |
| [Boltons](https://github.com/mahmoud/boltons) `clamp` | `d31d821d56c8f7756af9c30a864077f2e5855321` | Swap nested `min` / `max` | 621 passed, 22 subtests passed | 2 failed, 619 passed |
| [More Itertools](https://github.com/more-itertools/more-itertools) `take` | `08dca284ba5446a026afdc34067b658f9da989eb` | Take `n + 1` elements | 769 passed, 21,194 subtests passed | 33 failed, 736 passed |

The passing upstream and failing mutant suites were recorded by [this Docker
preflight run](https://github.com/AnanthKumar1810/agentforge/actions/runs/41211198361).
The first live Ollama attempts exhausted the Developer's eight file-tool turns
without making a change. A 16 turn run showed repeated successful searches
and reads on More Itertools, and exact-match replacement failures on Boltons;
neither was a successful repair. A new `replace_line` tool and focused prompts
now address those observed issues. Check the newest `pinned-repository-benchmark`
GitHub Actions run and its per-case `ollama-repair-*` artifacts for current
live outcomes. These requests identify the affected function and suggest a
numbered-line edit, so passing them would show guided repair, not blind diagnosis.

Reproduce on a Docker host with the validator image and a local Ollama model:

```bash
python -m pip install -e ".[dev]"
docker build -t agentforge-validator:py313 validator
python -m benchmarks.repository_mutations
ollama pull qwen2.5-coder:7b
python -m benchmarks.repository_mutations --model qwen2.5-coder:7b --output .agentforge-data/benchmark-live
```

Use a new output directory for each run; the script refuses to reuse its
disposable checkout. The real-model result requires `repair=passed` and a
verified Docker test suite for each case. Two curated mutations cannot establish
general production repair quality.

