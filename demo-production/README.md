# Flip hackathon demo assets

This folder packages the sanitized public Site and an 82-second historical
highlight for PR #11. The Site was created and published with Codex Sites:
[Flip Incident Report — Green Check](https://flip-green-check-report-2026.dualheron.chatgpt.site/).

## Video

- `video/out/flip-hackathon-highlight.mp4` — H.264, 1920×1080, 30 fps, 82 seconds. Silent; there is no voiceover or caption track.
- `video/public/thumbnail.png` — 1280×720 thumbnail.
- `video/recordings/raw/incident-evidence-tour.webm` — unedited browser capture of the published Site and public PR #11 diff.
- `video/` contains the Remotion project source. It uses the MIT-licensed `digitalsamba/claude-code-video-toolkit` at commit `04d5f46067cf301e722d29b4aac68b260f4b10cb`; see `video/UPSTREAM-LICENSE.txt`.
- `docs/final-timestamps.md` lists the exact chapter boundaries and identifies which scenes show historical evidence.

## Verification status

The PR #11 browser footage and incident Site are historical evidence. The video
does not show a new Switch run, two-user Codex session, fresh engineering task,
or current QA/security handoff. The full 9–12 minute live demo was not produced
because the Switch agent was unreachable, the local Docker runtime was down,
and a second real participant was unavailable. TTS credentials were also
unavailable, so the rendered highlight is silent. See `docs/feature-matrix.md`
and `docs/recording-plan.md` for evidence and recovery requirements.

## Source references

- [PR #11](https://github.com/Abhigyan-Shekhar/flip/pull/11)
- [Investigated revision](https://github.com/Abhigyan-Shekhar/flip/commit/c26948e4d4205b45d54a2faad86fb486d78576f3)
- [Fix revision](https://github.com/Abhigyan-Shekhar/flip/commit/b54ff14dacf830ab808f6d58afacff9649ec2b99)
- [Merged revision](https://github.com/Abhigyan-Shekhar/flip/commit/37d125330ddd0dc603da92a442e796cd2542d683)
