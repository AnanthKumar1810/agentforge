# Pinned repository repair benchmark

AgentForge has exercised five independently maintained Python repositories at
exact upstream commits. The [November 1 CI run](https://github.com/AnanthKumar1810/agentforge/actions/runs/41267363640)
confirmed that all five original test suites pass and each injected one-line
regression makes its suite fail. A local `qwen2.5-coder:3b` repair was then
attempted in each case. **One of five repairs passed** its full suite in Docker.
The other four made no accepted source edit. This is a guided repair benchmark,
not a measure of blind bug discovery: the prompt includes the affected file,
injected fragment, and expected original fragment.

| Case | Pinned upstream commit | Injected regression | Latest repair |
| --- | --- | --- | --- |
| [Boltons](https://github.com/mahmoud/boltons) `clamp` | `d31d821d56c8f7756af9c30a864077f2e5855321` | Swap nested `min` / `max` | No accepted edit |
| [More Itertools](https://github.com/more-itertools/more-itertools) `take` | `08dca284ba5446a026afdc34067b658f9da989eb` | Take `n + 1` elements | No accepted edit |
| [ItsDangerous](https://github.com/pallets/itsdangerous) compressed payload | `672971d66a2ef9f85151e53283113f33d642dabd` | Change compression marker | No accepted edit |
| [Iniconfig](https://github.com/pytest-dev/iniconfig) key whitespace | `a9e0ad14bcd0d9d3b68d2b699ce38399cbe64fdd` | Stop stripping key names | **Verified repair** |
| [MarkupSafe](https://github.com/pallets/markupsafe) proxy escaping | `4e111271b1494995708a9e878a10829b5e10c7ba` | Use `isinstance` in escape path | No accepted edit |

The verified Iniconfig repair restored `key_name = name.strip()` in
`src/iniconfig/_parse.py`; its full suite passed in the restricted Docker
validator. CI artifacts preserve per-case baseline, mutation, repair response,
diff, and validation result. The earlier [Docker preflight](https://github.com/AnanthKumar1810/agentforge/actions/runs/41211198361)
also confirmed passing upstream and failing mutations for Boltons and More
Itertools.

## Reproduce

On a Docker host with a local Ollama service:

```bash
python -m pip install -e ".[dev]"
docker build -t agentforge-validator:py313 validator
python -m benchmarks.repository_mutations --output .agentforge-data/preflight
ollama pull qwen2.5-coder:3b
python -m benchmarks.repository_mutations --model qwen2.5-coder:3b --fast --output .agentforge-data/repair
```

Use new output directories for each run; the benchmark refuses to reuse a
disposable checkout. The real-model result requires `repair.status=passed`, a
target-only source diff, and a verified Docker test suite. The validator runs
downloaded code with network disabled, a read-only workspace mount, dropped
privileges, and CPU, memory, process, and time limits. The GitHub runners are
disposable.

## Next steps for larger repositories

Measure repeated runs and report pass rate, runtime, and timeouts. Add larger
repositories only after recording their pinned commits, dependency needs,
baseline pass, and mutation failure. Improve file navigation and bounded edit
selection so the model can act without being given the exact original line.
Workspace search now streams its first 1,000 Python paths instead of building
an unbounded path list; broader indexed navigation remains future work.
Raise import, file, and validation budgets deliberately per case; the current
limits and the Docker image are tuned for small Python repositories. Keep the
full maintained suite as the final gate. Five curated mutations and one guided
model repair do not establish general repair reliability or production safety.
