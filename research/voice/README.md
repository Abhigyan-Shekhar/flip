# Isolated Python voice dependency investigation

October 29, 2026. This is reusable dependency preparation, with no Switch rooms,
application agents, Discord connections, microphone access, or voice recording.
It does not implement SwitchCrew's competition workflow.

## Candidates examined

| Candidate | Finding | Decision |
| --- | --- | --- |
| discord.py 2.7.1 + published discord-ext-voice-recv 0.5.2a179 | Base library supports DAVE; published receive extension lacks the second decryption layer | Do not use the published extension as-is |
| py-cord[voice] 2.8.1 | Released receive code calls DAVE, but its AEAD path unconditionally removes eight bytes | Rejected by the no-extension RTP boundary probe below |
| py-cord PR #3159 at `10a5e8cf13bd8db21f95baf62cf866056d1e1091` | Removes unconditional slicing; handles actual header/padding layout | Candidate for isolated live validation; unmerged, not production-qualified |
| discord-ext-voice-recv PR #62 / #65 | Alternative unmerged DAVE receive implementations | Source reviewed; not installed or tested |
| jstewart0788 receive fork at `78fcb434a3484f2abf54cf89e80e86b651e5c28d` | Adds DAVE handling but also enables passthrough in decoder construction | Not selected for this probe |

The py-cord PR remained open and unmerged when checked through GitHub's API.
Pin the exact commit rather than a moving PR ref. Its published package version
can differ from its source snapshot; the commit is the candidate identity.
Do not install py-cord and discord.py in the same virtual environment: both own
the `discord` namespace. This is another reason to isolate the voice process
from Switch's own Python dependencies.

Sources: [py-cord PR #3159](https://github.com/Pycord-Development/pycord/pull/3159),
[PyPI release metadata](https://pypi.org/pypi/py-cord/2.8.1/json),
[receive PR #62](https://github.com/imayhaveborkedit/discord-ext-voice-recv/pull/62),
[receive PR #65](https://github.com/imayhaveborkedit/discord-ext-voice-recv/pull/65),
and [fork source](https://github.com/jstewart0788/discord-ext-voice-recv-dave/tree/78fcb434a3484f2abf54cf89e80e86b651e5c28d).

## What the probe proves

The tests use real PyNaCl XChaCha20-Poly1305 authenticated transport packets with
a valid RTP header whose extension bit is unset. A DAVE spy checks that the
**entire** authenticated inner payload and the correct sender/media type arrive
at the DAVE boundary. It returns a known Opus frame, which the final test decodes
with the real native Opus library. A corrupt transport packet must be rejected
before reaching DAVE. Unknown senders must not select a decryption key, and a
replacement DAVE session must be read on the next packet.

With py-cord 2.8.1, the payload's first eight bytes are lost in
`PacketDecryptor._decrypt_rtp_aead_xchacha20_poly1305_rtpsize`. The probe observed
`synthetic-dave-ciphertext-for-boundary-test` becoming
`c-dave-ciphertext-for-boundary-test`. Three assertions failed for that same
root cause; two passed. This demonstrates a valid no-extension RTP case, not
that every packet in every current Discord call has that layout.

**The DAVE session is a spy, not real MLS encryption.** These tests do not prove
Discord handshake, key exchange, live sender mapping, packet loss recovery,
extended RTP headers, membership/key transitions, reconnect, or duplex audio.
Do not report offline success as a working voice integration. Those live cases
remain necessary even if all boundary checks pass.

## Reproduce

Run from the repository root. Use distinct environments. Native Opus must be
installed; the tests discover the existing Apple Silicon Homebrew path, or use
`OPUS_LIBRARY` for a different installation. No package is installed globally.

```sh
uv venv --python 3.12 /tmp/switchcrew-voice-release
uv pip install --python /tmp/switchcrew-voice-release/bin/python -r research/voice/requirements-release.txt
/tmp/switchcrew-voice-release/bin/python -m pytest research/voice/test_receive_contract.py -q
```

The release run is expected to fail three checks. It is a diagnostic reproducer,
not a passing application test suite.

```sh
uv venv --python 3.12 /tmp/switchcrew-voice-candidate
uv pip install --python /tmp/switchcrew-voice-candidate/bin/python -r research/voice/requirements-candidate.txt
/tmp/switchcrew-voice-candidate/bin/python -m pytest research/voice/test_receive_contract.py -q
```

For a different native library location:

```sh
OPUS_LIBRARY=/absolute/path/to/libopus.so /tmp/switchcrew-voice-candidate/bin/python -m pytest research/voice/test_receive_contract.py -q
```

During this investigation, uv's VCS dependency fetch stalled. The candidate was
instead installed from a detached local Git worktree of the already-fetched
exact commit in the research clone. An archive-only build failed because the
upstream build requires Git metadata for its package version. The Git worktree
preserves that metadata. This changes installation transport, not tested source.

## Required live acceptance gate

Use a dedicated bot application, test guild/channel, and explicitly consenting
participants. Configure credentials locally without posting tokens in chat.
Start no capture before consent. Keep the spike isolated from task creation and
Switch. Retain only the audio needed for consented verification, then delete it.

1. Connect to a DAVE-enabled channel; confirm a real DAVE session is ready.
2. Receive intelligible decoded PCM from two distinct consenting speakers.
3. Send a short test reply and confirm it is audible, without feedback capture.
4. Add/remove a consenting participant and verify recovery after key changes.
5. Disconnect/reconnect and confirm sender mappings and decryption recover.
6. Stop capture and verify callbacks, queues, and files stop accepting audio.

Only then select the voice stack for SwitchCrew and test STT/TTS separately.
The dependency remains provisional until these checks pass.

## Recorded offline results

On CPython 3.12.13, macOS Apple Silicon, with the existing native library at
`/opt/homebrew/lib/libopus.dylib`:

- Published py-cord 2.8.1: **2 passed, 3 failed**, exit code 1. The three failures
  all assert preservation of the full inner DAVE payload.
- Pinned PR `10a5e8cf13bd8db21f95baf62cf866056d1e1091`: **5 passed**, exit code 0.
  Installed build reported `0.1.dev1+g10a5e8cf1` because the local Git checkout
  was shallow; the exact commit identifies this candidate.

Neither run connected to Discord or tested real DAVE cryptography. Both used
`davey==0.1.7`, PyNaCl 1.6.2, and pytest 9.2.0. The tests exercised real AEAD
transport authentication and native Opus encoding/decoding with a DAVE spy.

## Other readiness items

- Docker: user requested **skip for now**. Docker Desktop/CLI is absent. Podman
  5.7.1 exists; its `company-brain` AppleHV VM is stopped. Its configured socket
  refused connections. No VM was started, installed, or reconfigured.
- Switch: real integration remains event-day M2. Verify the actual server,
  two independently registered identities, human account mapping, addressed
  handoff, same-thread response, and reconnect evidence. No protocol simulation
  substitutes for this check.
- Codex and QA: CLI availability is established, not execution. M4 must show
  an exact revision, independent sandboxed QA failure/correction/pass, and an
  authorized real draft PR. Keep QA and publication credentials isolated from
  engineering. The development assistant's own test run is not evidence of a
  separate QA agent.
