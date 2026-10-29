"""Isolated upstream dependency checks. No Discord connection or audio capture.

Transport encryption and Opus encoding are real. The DAVE session is a spy:
these tests verify the receive boundary, not MLS negotiation or live voice.
"""

import os
import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import davey
import pytest
from discord import opus
from discord.voice.packets.rtp import RTPPacket
from discord.voice.receive.reader import PacketDecryptor
from nacl.exceptions import CryptoError
from nacl.secret import Aead

KEY = bytes(range(32))  # Synthetic test key; never used outside these tests.
SENDER = 123456789
SSRC = 42
FRAME = b"synthetic-dave-ciphertext-for-boundary-test"


def wire_packet(payload=FRAME):
    header = struct.pack(">BBHII", 0x80, 0x78, 1, 960, SSRC)
    suffix = struct.pack(">I", 7)
    encrypted = Aead(KEY).encrypt(payload, header, suffix + bytes(20)).ciphertext
    return header + encrypted + suffix


def receiver(*, ready=True, mapped=True, plaintext=b"decoded-opus"):
    decrypt = Mock(return_value=plaintext)
    session = SimpleNamespace(ready=ready, decrypt=decrypt)
    state = SimpleNamespace(
        dave_session=session, ssrc_user_map={SSRC: SENDER} if mapped else {}
    )
    client = SimpleNamespace(_connection=state)
    return PacketDecryptor("aead_xchacha20_poly1305_rtpsize", KEY, client), session


def test_transport_preserves_entire_dave_frame():
    decryptor, session = receiver()
    assert decryptor.decrypt_rtp(RTPPacket(wire_packet())) == b"decoded-opus"
    session.decrypt.assert_called_once_with(SENDER, davey.MediaType.audio, FRAME)


def test_invalid_transport_authentication_never_reaches_dave():
    decryptor, session = receiver()
    packet = bytearray(wire_packet())
    packet[15] ^= 1
    with pytest.raises(CryptoError):
        decryptor.decrypt_rtp(RTPPacket(bytes(packet)))
    session.decrypt.assert_not_called()


def test_unknown_sender_does_not_attempt_dave_decryption():
    decryptor, session = receiver(mapped=False)
    assert not decryptor.decrypt_rtp(RTPPacket(wire_packet()))
    session.decrypt.assert_not_called()


def test_new_session_is_used_after_membership_transition():
    decryptor, old_session = receiver(plaintext=b"old-epoch")
    assert decryptor.decrypt_rtp(RTPPacket(wire_packet())) == b"old-epoch"
    replacement = SimpleNamespace(ready=True, decrypt=Mock(return_value=b"new-epoch"))
    decryptor.client._connection.dave_session = replacement
    assert decryptor.decrypt_rtp(RTPPacket(wire_packet())) == b"new-epoch"
    assert old_session.decrypt.call_count == 1
    replacement.decrypt.assert_called_once_with(SENDER, davey.MediaType.audio, FRAME)


def test_decoded_opus_reaches_native_pcm_decoder():
    if not opus.is_loaded():
        candidates = [os.environ.get("OPUS_LIBRARY"), "/opt/homebrew/lib/libopus.dylib"]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                opus.load_opus(candidate)
                break
    if not opus.is_loaded():
        pytest.fail("Set OPUS_LIBRARY to the installed native Opus library")
    pcm = bytes(opus.Encoder.FRAME_SIZE)
    encoded = opus.Encoder().encode(pcm, opus.Encoder.SAMPLES_PER_FRAME)
    decryptor, session = receiver(plaintext=encoded)
    recovered = decryptor.decrypt_rtp(RTPPacket(wire_packet()))
    decoded = opus.Decoder().decode(recovered, fec=False)
    assert len(decoded) == len(pcm)
    session.decrypt.assert_called_once_with(SENDER, davey.MediaType.audio, FRAME)
