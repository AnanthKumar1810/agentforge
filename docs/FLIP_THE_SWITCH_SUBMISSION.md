# Flip the Switch submission notes

## Entry

**Project:** AgentForge — human-approved code repair in Switch

**Track:** Best Business Workflow

**Problem:** Teams need an agent to investigate a failing test without silently changing code.
**Workflow:** A person reports a failing test in a Switch-connected room. AgentForge stages a candidate repair, runs checks in isolation, and posts a diff and evidence. A named reviewer approves or rejects through Switch. Only approval applies the unchanged patch; AgentForge reruns checks and reports the result.

AgentForge's original repair engine predates the event. The Switch client and approval workflow were built for this hackathon. Switch is required for live intake, reviewer decision, and evidence delivery.

## Reproducible live demo (about 2 minutes)

1. Use the included `examples/repair_target` fixture. Show `sample.py` returning `0` and its failing `tests/test_sample.py` expecting `2`. Show the failing test.
2. In a Switch room backed by Mattermost or another supported channel, address AgentForge: `repair: test_value fails; value() must return 2`.
3. Show AgentForge's posted patch, SHA-256 digest, run ID, and pre-approval tests. Show that `sample.py` still returns `0` in the target.
4. From a different, unauthorized participant, try `approve <repair-id>` and show it is refused. From the configured reviewer, send `approve <repair-id>`.
5. Show `sample.py` now returning `2` and AgentForge's final diff and post-approval test logs. Optionally repeat the same approval to show it has no second effect.

Use a clean, trusted fixture for the video. Do not display API keys or local `.env` files. Confirm that the room, model provider, and Docker validator are running before recording.

## Submission checklist

- [ ] Live Switch room and custom agent created; agent API key stored privately
- [ ] Reviewer participant ID configured and verified with an actual room message
- [ ] Full live flow recorded, including source unchanged before approval and tests after approval
- [ ] Public repository link checked from a signed-out browser
- [ ] Demo video published and link checked from a signed-out browser
- [ ] Final entry submitted manually by the registered participant before **November 7, 2026, 11:59 PM IST**

Registration was reported complete by the participant. This document does not assert that the Switch account, credentials, demo video, or final entry exist yet. Official event requirements and deadline: [Flip the Switch](https://www.wemakedevs.org/hackathons/switch). Custom agent wire contract: [Switch agent protocol](https://docs.switchagents.ai/switch-rooms/internals/agent-protocol).

## Verified development evidence (November 7, 2026)

- The targeted offline suite passed locally: **35 passed, 1 skipped**. It covers reviewer authorization, rejection, replay, source drift, and reviewable text patches.
- `python -m examples.demo_repair` passed locally with two attempts and one repair; the original fixture remained unchanged.
- The existing GitHub Actions `tests` run for PR commit `577f87c` completed successfully. The new Docker validator job must be checked after this update is pushed.
- The local Docker engine was unavailable during this check. The new Docker test has not yet been claimed as a local pass.
- No Switch bridge, room, agent API key, or reviewer participant ID was present in this environment. No live Switch message flow or video is claimed.

## Portal submission copy

**Title:** AgentForge — human-approved code repair through Switch

**Track:** Best Business Workflow

**Description:** AgentForge brings a software repair agent into a Switch-connected team room. A teammate reports a failing test; the agent stages a candidate patch and posts the complete diff, hash, and test evidence without touching the source. A named human reviewer approves or rejects in the room. Approval checks that the source and staged files still match what was reviewed, applies the patch once, and posts fresh validation results. The project includes a reproducible fixture, offline approval tests, and a Docker validator check. The existing repair engine predates the event; the Switch client and human approval workflow were added for this hackathon.

**Repository:** https://github.com/AnanthKumar1810/agentforge (merge PR #3 to `main` before using this link as the final code)

**Demo video:** Add the public link only after recording and verifying a real Switch room run. Do not use the offline test output as a live demo.

The [official schedule](https://www.wemakedevs.org/hackathons/switch/schedule) requires the repository, README, and demo video by **November 7, 2026, 11:59 PM IST**. The [official rules](https://www.wemakedevs.org/hackathons/switch/rules) prohibit automated submissions; the registered entrant must submit in the portal personally.
