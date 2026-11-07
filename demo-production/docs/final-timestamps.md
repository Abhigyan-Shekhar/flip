# Final highlight timestamps

Source: `video-toolkit/projects/flip-hackathon-final-demo/out/flip-hackathon-highlight.mp4`.
Duration was confirmed as 82.000 seconds with ffprobe. Times below follow the
Remotion scene boundaries in the rendered 1920×1080, 30 fps file.

| Start–end | Scene | Recorded action / on-screen evidence | Narration script (not recorded) | Evidence status |
|---|---|---|---|---|
| 00:00–00:08 | The green check that lied | Historical title card, dated 6 November 2026 | On November sixth, Flip fixed a green check that the scanner report had not earned. | Historical PR story |
| 00:08–00:21 | Incomplete report | Conceptual JSON and missing report identity fields; marked conceptual on screen | An empty findings list is not proof of a clean scan when the native report envelope is incomplete. The report still has to identify its schema, artifact, artifact type, and Trivy version. | Conceptual visual, supported by PR diff |
| 00:21–00:32 | Fail-closed contract | Required native envelope fields and normalized-format compatibility | The fix validates the native Trivy envelope and keeps the normalized scanner contract working. | Historical fix summary; no fresh execution |
| 00:32–01:04 | Public incident evidence | Real browser recording of the published Site, then GitHub PR #11 Files changed showing the code diff | This is the public incident report and merged PR eleven. The linked revisions show what was investigated, what changed, and where the fix landed. This is historical evidence, not a fresh run. | Historical browser capture, 7 November 2026 |
| 01:04–01:14 | Regression contract | Test coverage categories summarized on a feature card | Regression cases cover missing or malformed fields, findings and clean reports, and compatibility with normalized results. | Test descriptions from historical PR; not run today |
| 01:14–01:22 | Evidence before green | Final CTA with public PR URL | PR eleven merged on November sixth. Evidence before green. | Historical merge reference |

**Audio and captions:** no voiceover or subtitles are included. The narration
column is script only because supported TTS credentials were unavailable. This
is an 82-second historical highlight, not the requested full live demo.
