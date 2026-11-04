# Flip the Switch submission notes

## Entry

**Project:** AgentForge — human-approved code repair in Switch

**Track:** Best Business Workflow

**Problem:** Teams need an agent to investigate a failing test without silently changing code.
**Workflow:** A person reports a failing test in a Switch-connected room. AgentForge stages a candidate repair, runs checks in isolation, and posts a diff and evidence. A named reviewer approves or rejects through Switch. Only approval applies the unchanged patch; AgentForge reruns checks and reports the result.

AgentForge's original repair engine predates the event. The Switch client and approval workflow were built for this hackathon. Switch is required for live intake, reviewer decision, and evidence delivery.

## Reproducible live demo (about 2 minutes)

1. Show a clean demo target with `sample.py` returning `0` and a failing `test_value.py` expecting `2`. Show the failing test.
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
