import socket

import pytest

from src.peer.connection import PeerConnection
from src.peer.peer import Peer
from src.peer.protocol import (
    MessageType,
    PeerState,
    decode_bitfield,
    decode_piece,
    encode_request,
)
from src.swarm.peer_server import PeerServer
from tests.conftest import SWARM_ID


def _connect(swarm_file, port, swarm_id=SWARM_ID, peer_id="probe"):
    """Open a connection and run the client handshake with the real Peer."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=3.0)

    peer = Peer(peer_id, swarm_id, swarm_file.metainfo)
    peer.connection = PeerConnection(sock)
    peer.state = PeerState.HANDSHAKING
    peer.perform_client_handshake()

    return peer


def test_server_advertises_every_piece_by_default(swarm_file, start_server):
    server = start_server("seed")
    peer = _connect(swarm_file, server.port)

    message_type, payload = peer.receive_framed_message()
    count = swarm_file.metainfo.piece_count

    assert message_type == MessageType.BITFIELD
    assert decode_bitfield(payload, count) == [True] * count

    peer.connection.close()


def test_server_advertises_only_the_pieces_it_has(swarm_file, start_server):
    count = swarm_file.metainfo.piece_count
    have = [index % 3 == 0 for index in range(count)]

    server = start_server("partial", have=have)
    peer = _connect(swarm_file, server.port)

    message_type, payload = peer.receive_framed_message()

    assert decode_bitfield(payload, count) == have

    peer.connection.close()


def test_server_serves_an_available_piece(swarm_file, start_server):
    server = start_server("seed")
    peer = _connect(swarm_file, server.port)
    peer.receive_framed_message()

    count = swarm_file.metainfo.piece_count
    peer.send_framed_message(MessageType.REQUEST, encode_request(3, count))

    message_type, payload = peer.receive_framed_message()
    index, data = decode_piece(payload, count)

    assert message_type == MessageType.PIECE
    assert index == 3
    assert data == swarm_file.data[3 * 64:4 * 64]

    peer.connection.close()


def test_server_closes_connection_on_unavailable_piece(swarm_file, start_server):
    count = swarm_file.metainfo.piece_count
    server = start_server("partial", have=[index == 0 for index in range(count)])

    peer = _connect(swarm_file, server.port)
    peer.receive_framed_message()

    peer.send_framed_message(MessageType.REQUEST, encode_request(5, count))

    try:
        received = peer.receive_framed_message()
    except Exception:
        received = None

    assert received is None


def test_server_handles_many_connections_at_once(swarm_file, start_server):
    server = start_server("seed")
    count = swarm_file.metainfo.piece_count

    peers = [_connect(swarm_file, server.port, peer_id=f"p{i}") for i in range(6)]

    for peer in peers:
        message_type, _ = peer.receive_framed_message()
        assert message_type == MessageType.BITFIELD

    for index, peer in enumerate(peers):
        peer.send_framed_message(MessageType.REQUEST, encode_request(index, count))

    for index, peer in enumerate(peers):
        _, payload = peer.receive_framed_message()
        assert decode_piece(payload, count)[0] == index
        peer.connection.close()


def test_server_rejects_wrong_swarm(swarm_file, start_server):
    server = start_server("seed")
    peer = _connect(swarm_file, server.port, swarm_id="some-other-swarm")

    assert peer.state == PeerState.CLOSED


def test_server_keeps_running_after_a_client_disconnects(swarm_file, start_server):
    server = start_server("seed")

    first = _connect(swarm_file, server.port)
    first.receive_framed_message()
    first.connection.close()

    second = _connect(swarm_file, server.port)
    message_type, _ = second.receive_framed_message()

    assert message_type == MessageType.BITFIELD

    second.connection.close()


def test_server_rejects_have_of_wrong_length(swarm_file):
    with pytest.raises(ValueError):
        PeerServer("seed", SWARM_ID, swarm_file.metainfo, swarm_file.path, have=[True])
