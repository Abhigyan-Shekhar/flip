# Feature verification matrix

| Capability | Evidence | Status for this production run |
|---|---|---|
| Trivy envelope fix | Public PR #11 and merged commits `c26948e`, `b54ff14`, and `37d1253` | Verified historical; no fresh vulnerable/fixed execution |
| Discord PM/Security handoff | Authenticated `#flip-test` view contains dated 6 November messages | Historical only; no new Discord message or handoff recorded |
| Switch agent acknowledgment/results | Historical messages show acceptance and a later configured-check result | Historical only; Switch Console reports no live sessions and its selected agent is unreachable |
| Two independent Codex users | Second participant unavailable today | Blocked; not simulated |
| Company Brain | Earlier project documentation describes revision-grounded lookup | Historical implementation evidence; not used live in this recorded cut |
| Waggle memory | Waggle project memory is available | Used by the assistant for context; no claimed effect on a new engineering task |
| Operator dashboard and controls | Source and documentation exist | Implemented/documented; not live demonstrated |
| Codex Sites | Public incident report opened and verified; URL is `https://flip-green-check-report-2026.dualheron.chatgpt.site/` | Published through Codex Sites; this is a Codex capability, not a Flip feature |
| GitHub PR and merge | Public PR #11 is merged at `37d1253` | Verified historical result; no new PR created |
| Highlight video | Actual browser capture of the Site and PR diff; 82-second Remotion render | Rendered, silent, and historical; no live Flip run |
| Full 9–12 minute demo | Requires live Switch runtime, Docker-backed services, and a second participant | Not produced |

Do not upgrade a historical or code-only row to “live verified” without new
recorded evidence.
