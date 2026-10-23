import pytest

from src.peer.protocol import (
    Handshake,
    MessageType,
    PeerState,
    PROTOCOL_ID,
    validate_handshake,
    encode_handshake,
    decode_handshake,
    encode_message,
    decode_message,
)


def test_valid_handshake_validation():

    handshake = Handshake(
        protocol_id=PROTOCOL_ID,
        swarm_id="swarm-001",
        peer_id="peer-A",
    )

    assert validate_handshake(
        handshake,
        PROTOCOL_ID,
        "swarm-001",
    )


def test_invalid_protocol():

    handshake = Handshake(
        protocol_id="WRONG-PROTOCOL",
        swarm_id="swarm-001",
        peer_id="peer-A",
    )

    assert not validate_handshake(
        handshake,
        PROTOCOL_ID,
        "swarm-001",
    )


def test_invalid_swarm():

    handshake = Handshake(
        protocol_id=PROTOCOL_ID,
        swarm_id="wrong-swarm",
        peer_id="peer-A",
    )

    assert not validate_handshake(
        handshake,
        PROTOCOL_ID,
        "swarm-001",
    )


def test_empty_peer_id():

    handshake = Handshake(
        protocol_id=PROTOCOL_ID,
        swarm_id="swarm-001",
        peer_id="",
    )

    assert not validate_handshake(
        handshake,
        PROTOCOL_ID,
        "swarm-001",
    )


def test_handshake_round_trip():

    original = Handshake(
        protocol_id=PROTOCOL_ID,
        swarm_id="swarm-001",
        peer_id="peer-A",
    )

    encoded = encode_handshake(original)

    decoded = decode_handshake(encoded)

    assert decoded.protocol_id == original.protocol_id
    assert decoded.swarm_id == original.swarm_id
    assert decoded.peer_id == original.peer_id


def test_message_round_trip():

    payload = b"phase-2-test"

    encoded = encode_message(
        MessageType.HANDSHAKE,
        payload,
    )

    message_type, decoded_payload = decode_message(
        encoded
    )

    assert message_type == MessageType.HANDSHAKE
    assert decoded_payload == payload


def test_peer_states():

    assert PeerState.DISCONNECTED.value == "disconnected"
    assert PeerState.CONNECTING.value == "connecting"
    assert PeerState.HANDSHAKING.value == "handshaking"
    assert PeerState.CONNECTED.value == "connected"
    assert PeerState.CLOSED.value == "closed"