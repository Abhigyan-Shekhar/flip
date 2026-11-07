# Narration draft — not yet timestamped

These lines are a draft, not narration from a completed video. The final
timestamp table will be populated from the rendered cut.

## Opening — planned

We already use Discord to communicate. We ask questions, share updates, review
work, and make decisions there. But our AI agents usually sit in separate
terminals. What if they could join the same conversation?

## Two-user scene — blocked until a second real participant joins

Two developers. Two separate Codex sessions, each running under its own account.
Through Switch, one agent can ask for help and another can investigate and return
findings. Discord is where people see and shape the collaboration; Switch carries
the addressed agent handoff.

Do not record or narrate this scene as completed until the two sessions and the
actual handoff are shown.

## Green Check incident — historical wording

On November 6, the team investigated a Trivy adapter that could treat an
incomplete native report as clean. The change was tied to the investigated
revision, added regression coverage, and was reviewed in PR #11. The fix commit
was `b54ff14`; merge `37d1253` completed the PR. This is historical evidence, not
a new run today.

## Highlight cut — final 82-second script

This text is script only. No TTS narration was rendered because neither
ElevenLabs nor Qwen3-TTS credentials are available in this workspace.

- 00:00–00:08 — On November sixth, Flip fixed a green check that the scanner report had not earned.
- 00:08–00:21 — An empty findings list is not proof of a clean scan when the native report envelope is incomplete. The report still has to identify its schema, artifact, artifact type, and Trivy version.
- 00:21–00:32 — The fix validates the native Trivy envelope and keeps the normalized scanner contract working.
- 00:32–01:04 — This is the public incident report and merged PR eleven. The linked revisions show what was investigated, what changed, and where the fix landed. This is historical evidence, not a fresh run.
- 01:04–01:14 — Regression cases cover missing or malformed fields, findings and clean reports, and compatibility with normalized results.
- 01:14–01:22 — PR eleven merged on November sixth. Evidence before green.

The exact action and narration timing for every scene is in
[`final-timestamps.md`](final-timestamps.md). The script was not recorded as
voiceover, so these are not audio-synchronized captions.

## Architecture — planned

Discord is the human-facing workspace. Switch is the agent communication layer.
LangGraph coordinates an individual agent's workflow. Flip adds durable task
state, verification, memory, and human approval boundaries.

## Incident report — planned

Codex can turn the verified public incident facts into a compact report site.
We preview it, check that it contains only public and approved evidence, publish
it through Codex Sites, and verify the resulting URL. Sites is a Codex capability
used in the workflow; it is not a feature built into Flip.

## Close — planned

We already work together in Discord. Flip gives our AI agents a way to join that
collaboration, with independent checks and people still in control. This is Flip.
