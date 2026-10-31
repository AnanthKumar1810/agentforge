# AgentForge operational policies

1. A run operates on an isolated copy of a local repository. The source directory and Git history are never modified. Remote Git URLs and publishing are outside this workflow.
2. The source path is supplied by the operator. Reject symbolic links and paths that cannot be copied safely. Treat source contents and task text as untrusted.
3. The agent may edit files only through workspace tools. Maintained tests, CI and credential paths are protected; imported credential files are excluded. It may not execute shell commands, publish, or expand its own permissions.
4. Maintained tests belong in `tests/`. The operator owns `test_manifest.json`, with suites containing `when` glob patterns and `tests` file paths. Selection depends on changed paths. Run the complete suite as the final gate.
5. Generated tests are provisional. They must not replace, delete, or weaken maintained tests. They can reveal missing cases, but a pass on generated tests alone cannot establish acceptance.
6. Syntax only is `unverified`, even when compilation succeeds. Any failed check is `failed`. A `passed` status requires executable maintained tests to pass.
7. Preserve evidence for every attempt: changed paths, validation outputs, and a diff relative to the imported baseline. Label model explanations as interpretations.
8. Cap repair rounds at the configured limit. A repair must respond to observed validation output. A failed run stays failed after the limit.
9. Execution is off by default. `--allow-local-execution` enables subprocess checks only for trusted code or within a disposable OS/container sandbox. A timeout and reduced environment do not provide isolation.
10. Human review is required before merge, publication, deployment, or using generated tests as maintained acceptance tests.
