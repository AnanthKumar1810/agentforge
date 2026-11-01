# Reliability fixes verified on November 1, 2026

This update checks and hardens the existing seven fixes discussed before the competition.
It does not establish production readiness.

## Completed code paths

- Native Ollama tool requests and the supported JSON-text fallback execute workspace tools.
- Repeated writes are checked against current exact file content. Whitespace edits are preserved;
  an intervening change invalidates duplicate suppression. Every call in a batch is processed
  before a no-change batch hands control back to deterministic validation.
- Tool names, required arguments, types and batch size are checked. Invalid arguments return
  tool errors; malformed message envelopes fail explicitly. The 16-turn limit remains enforced.
- Deterministic validation owns the final status. A regression test demonstrates that an
  incorrect model success claim cannot override failed validation. Markdown labels model review
  as interpretation rather than validation evidence.
- The engine's runner setting and validator interface are compatible; local repair validation
  is exercised by the suite and offline demo.
- Saved JSON contains its artifact paths; tests read back the actual saved report.
- Direct local HTTP requests disable inherited proxy settings and close their response handles.
- Ollama uses its direct adapter without requiring the hosted-model SDK code path. This avoids
  the SDK dependency that Windows Application Control previously rejected. Windows policy itself
  has not been changed or retested from this Linux environment.

## Verification

- Full suite: 51 passed on Linux with Python 3.12.
- Offline demo: initial validation failed, bounded repair passed, source unchanged.
- Integration regression: the actual engine and direct Ollama adapter consume scripted provider
  responses, suppress a repeated edit, run real pytest, and save a verified diff/report.
  Scripted responses test the integration; they are not a new model-quality result.
- Python compilation and git whitespace checks passed.

## Remaining limits

Ollama and Docker are unavailable in this environment. No fresh real-model run or Docker
isolation verification was performed for this revision. The earlier Qwen live fixture result
remains historical evidence for its earlier revision. Broader model repair quality and
production deployment remain separate work.
