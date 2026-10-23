import socket
import threading
import time

import pytest

from src.peer.connection import PeerConnection
from src.peer.peer import Peer
from src.peer.pieces import assemble_pieces
from src.peer.protocol import (
    MessageType,
    PeerState,
    encode_bitfield,
    encode_piece,
)
from src.swarm.peer_manager import PeerManager
from src.tracker.client import TrackerClient
from tests.conftest import PIECE_SIZE, SWARM_ID


class FakePeer:
    """
    A scripted remote peer. It handshakes with the real Peer code, sends a
    BITFIELD, then lets the test read REQUESTs and answer them by hand.
    """

    def __init__(self, swarm_file, peer_id, have):
        self.swarm_file = swarm_file
        self.peer_id = peer_id
        self.have = have
        self.requests = []

        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        self.port = self._listener.getsockname()[1]
        self.peer = None

        self._ready = threading.Event()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        sock, _ = self._listener.accept()
        self.peer = Peer(self.peer_id, SWARM_ID, self.swarm_file.metainfo)
        self.peer.connection = PeerConnection(sock)
        self.peer.state = PeerState.HANDSHAKING
        self.peer.perform_server_handshake()
        self.peer.send_framed_message(
            MessageType.BITFIELD,
            encode_bitfield(self.have),
        )
        self._ready.set()

    def read_request(self, timeout=3.0):
        """Return the next requested index, or None if none arrives."""
        self._ready.wait(timeout)
        self.peer.connection.sock.settimeout(timeout)

        try:
            while True:
                message_type, payload = self.peer.receive_framed_message()

                if message_type == MessageType.REQUEST:
                    index = int.from_bytes(payload, "big")
                    self.requests.append(index)
                    return index

        except (socket.timeout, ConnectionError, OSError):
            return None

    def read_requests(self, count, timeout=3.0):
        return [self.read_request(timeout) for _ in range(count)]

    def send_piece(self, index, corrupt=False):
        data = self.swarm_file.data[index * PIECE_SIZE:(index + 1) * PIECE_SIZE]

        if corrupt:
            data = bytes(len(data))

        self.peer.send_framed_message(
            MessageType.PIECE,
            encode_piece(index, data),
        )

    def disconnect(self):
        self.peer.connection.close()
        self._listener.close()


@pytest.fixture
def fake_peer(swarm_file):
    fakes = []

    def _make(peer_id, have):
        fake = FakePeer(swarm_file, peer_id, have)
        fakes.append(fake)
        return fake

    yield _make

    for fake in fakes:
        if fake.peer is not None:
            fake.peer.connection.close()

        fake._listener.close()


def _wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if condition():
            return True

        time.sleep(0.02)

    return False


def _peers(*servers):
    return [{"host": "127.0.0.1", "port": s.port} for s in servers]


def _assert_file_matches(manager, swarm_file, tmp_path):
    output = tmp_path / "output.bin"

    assemble_pieces(
        manager.pieces_dir,
        str(output),
        swarm_file.metainfo.piece_count,
    )

    assert output.read_bytes() == swarm_file.data


# ---------------------------------------------------------
# Construction and connecting
# ---------------------------------------------------------

def test_pipeline_size_and_max_peers_must_be_positive(make_manager):
    with pytest.raises(ValueError):
        make_manager(pipeline_size=0)

    with pytest.raises(ValueError):
        make_manager(max_peers=0)


def test_defaults_support_six_peers(make_manager):
    assert make_manager().max_peers >= 6


def test_add_peer_reads_the_bitfield(swarm_file, start_server, make_manager):
    server = start_server("seed")
    manager = make_manager()

    assert manager.add_peer("127.0.0.1", server.port)
    assert manager.peer_count == 1
    assert manager.piece_manager.availability() == (
        [1] * swarm_file.metainfo.piece_count
    )


def test_add_peer_fails_when_nothing_is_listening(make_manager):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    assert make_manager().add_peer("127.0.0.1", port) is False


def test_add_peer_fails_for_a_different_swarm(start_server, make_manager):
    server = start_server("seed", swarm_id="another-swarm")
    manager = make_manager()

    assert manager.add_peer("127.0.0.1", server.port) is False
    assert manager.peer_count == 0


def test_same_remote_peer_is_not_added_twice(start_server, make_manager):
    server = start_server("seed")
    manager = make_manager()

    assert manager.add_peer("127.0.0.1", server.port) is True
    assert manager.add_peer("127.0.0.1", server.port) is False
    assert manager.peer_count == 1


def test_cannot_connect_to_a_peer_with_our_own_id(start_server, make_manager):
    server = start_server("leecher")

    assert make_manager(peer_id="leecher").add_peer("127.0.0.1", server.port) is False


def test_max_peers_is_enforced(start_server, make_manager):
    servers = [start_server(f"seed-{i}") for i in range(4)]
    manager = make_manager(max_peers=2)

    assert manager.connect_to_peers(_peers(*servers)) == 2
    assert manager.peer_count == 2


def test_six_concurrent_peers(start_server, make_manager):
    servers = [start_server(f"seed-{i}") for i in range(6)]
    manager = make_manager()

    assert manager.connect_to_peers(_peers(*servers)) == 6
    assert manager.peer_count == 6
    assert manager.piece_manager.availability()[0] == 6


# ---------------------------------------------------------
# Downloading
# ---------------------------------------------------------

def test_download_from_one_seeder(swarm_file, start_server, make_manager, tmp_path):
    server = start_server("seed")
    manager = make_manager()

    manager.connect_to_peers(_peers(server))
    manager.start()

    assert manager.wait_until_complete(timeout=10.0)
    _assert_file_matches(manager, swarm_file, tmp_path)


def test_multi_peer_download_uses_every_peer(
    swarm_file, start_server, make_manager, tmp_path
):
    """Six peers each hold different pieces; together they hold the file."""
    count = swarm_file.metainfo.piece_count

    servers = [
        start_server(
            f"peer-{n}",
            have=[index % 6 == n or index % 6 == (n + 1) % 6 and index % 2 == 0
                  for index in range(count)],
        )
        for n in range(6)
    ]

    manager = make_manager(pipeline_size=3)

    assert manager.connect_to_peers(_peers(*servers)) == 6
    manager.start()

    assert manager.wait_until_complete(timeout=15.0)
    _assert_file_matches(manager, swarm_file, tmp_path)

    assert sum(manager.received_from.values()) == count
    assert set(manager.received_from) == {f"peer-{n}" for n in range(6)}


def test_pieces_held_by_one_peer_come_from_that_peer(
    swarm_file, start_server, make_manager
):
    count = swarm_file.metainfo.piece_count
    first = start_server("first", have=[index < count // 2 for index in range(count)])
    second = start_server("second", have=[index >= count // 2 for index in range(count)])

    manager = make_manager()
    manager.connect_to_peers(_peers(first, second))
    manager.start()

    assert manager.wait_until_complete(timeout=10.0)
    assert manager.received_from == {
        "first": count // 2,
        "second": count - count // 2,
    }


def test_download_is_blocked_until_start(swarm_file, start_server, make_manager):
    server = start_server("seed")
    manager = make_manager()

    manager.connect_to_peers(_peers(server))

    assert manager.wait_until_complete(timeout=0.3) is False
    assert manager.piece_manager.local_bitfield() == (
        [False] * swarm_file.metainfo.piece_count
    )


def test_wait_returns_false_when_pieces_are_missing(
    swarm_file, start_server, make_manager
):
    count = swarm_file.metainfo.piece_count
    server = start_server("partial", have=[index != 4 for index in range(count)])
    manager = make_manager()

    manager.connect_to_peers(_peers(server))
    manager.start()

    assert manager.wait_until_complete(timeout=1.0) is False
    assert not manager.piece_manager.has_piece(4)
    assert manager.piece_manager.has_piece(3)


def test_wait_returns_false_when_every_peer_disconnects(
    swarm_file, start_server, make_manager
):
    server = start_server("seed")
    manager = make_manager()

    manager.connect_to_peers(_peers(server))
    server.stop()

    assert manager.wait_until_complete(timeout=5.0) is False
    assert manager.peer_count == 0


def test_close_is_safe_to_call_twice(start_server, make_manager):
    server = start_server("seed")
    manager = make_manager()
    manager.connect_to_peers(_peers(server))

    manager.close()
    manager.close()

    assert _wait_for(lambda: manager.peer_count == 0)


# ---------------------------------------------------------
# Rarest-first (observed on the wire)
# ---------------------------------------------------------

def test_requests_follow_rarest_first(swarm_file, fake_peer, make_manager):
    """
    Pieces 2 and 3 are held only by `wide`; pieces 0 and 1 by both peers.
    The rare pieces must be requested first.
    """
    count = swarm_file.metainfo.piece_count

    wide = fake_peer("wide", [index < 4 for index in range(count)])
    narrow = fake_peer("narrow", [index < 2 for index in range(count)])

    manager = make_manager(pipeline_size=2)

    assert manager.add_peer("127.0.0.1", wide.port)
    assert manager.add_peer("127.0.0.1", narrow.port)
    manager.start()

    assert wide.read_requests(2) == [2, 3]
    assert narrow.read_requests(2) == [0, 1]


# ---------------------------------------------------------
# Request pipelining (observed on the wire)
# ---------------------------------------------------------

def test_multiple_requests_are_outstanding_at_once(
    swarm_file, fake_peer, make_manager
):
    count = swarm_file.metainfo.piece_count
    fake = fake_peer("slow", [True] * count)

    manager = make_manager(pipeline_size=3)
    manager.add_peer("127.0.0.1", fake.port)
    manager.start()

    assert fake.read_requests(3) == [0, 1, 2]
    assert fake.read_request(timeout=0.3) is None
    assert manager.piece_manager.in_flight_count("slow") == 3


def test_pipeline_size_is_configurable(swarm_file, fake_peer, make_manager):
    count = swarm_file.metainfo.piece_count

    for size in (1, 4):
        fake = fake_peer(f"slow-{size}", [True] * count)
        manager = make_manager(peer_id=f"leecher-{size}", pipeline_size=size)
        manager.add_peer("127.0.0.1", fake.port)
        manager.start()

        assert fake.read_requests(size) == list(range(size))
        assert fake.read_request(timeout=0.3) is None


def test_pipeline_refills_when_a_piece_arrives(swarm_file, fake_peer, make_manager):
    count = swarm_file.metainfo.piece_count
    fake = fake_peer("slow", [True] * count)

    manager = make_manager(pipeline_size=2)
    manager.add_peer("127.0.0.1", fake.port)
    manager.start()

    assert fake.read_requests(2) == [0, 1]

    fake.send_piece(0)
    assert fake.read_request() == 2

    fake.send_piece(1)
    assert fake.read_request() == 3

    assert manager.piece_manager.in_flight_count("slow") == 2


def test_no_piece_is_requested_twice_across_peers(
    swarm_file, fake_peer, make_manager
):
    count = swarm_file.metainfo.piece_count
    peers = [fake_peer(f"fake-{i}", [True] * count) for i in range(3)]

    manager = make_manager(pipeline_size=4)

    for fake in peers:
        manager.add_peer("127.0.0.1", fake.port)

    manager.start()

    requested = []

    for fake in peers:
        requested += fake.read_requests(4)

    assert len(requested) == 12
    assert len(set(requested)) == 12


def test_pipelined_download_completes(
    swarm_file, start_server, make_manager, tmp_path
):
    servers = [start_server(f"seed-{i}") for i in range(3)]
    manager = make_manager(pipeline_size=8)

    manager.connect_to_peers(_peers(*servers))
    manager.start()

    assert manager.wait_until_complete(timeout=10.0)
    _assert_file_matches(manager, swarm_file, tmp_path)
    assert manager.piece_manager.in_flight_count("seed-0") == 0


# ---------------------------------------------------------
# Disconnections and bad data
# ---------------------------------------------------------

def test_pieces_in_flight_from_a_lost_peer_are_requested_again(
    swarm_file, fake_peer, make_manager
):
    count = swarm_file.metainfo.piece_count
    first = fake_peer("first", [True] * count)
    second = fake_peer("second", [True] * count)

    manager = make_manager(pipeline_size=2)
    manager.add_peer("127.0.0.1", first.port)
    manager.add_peer("127.0.0.1", second.port)
    manager.start()

    assert first.read_requests(2) == [0, 1]
    assert second.read_requests(2) == [2, 3]

    first.disconnect()

    assert _wait_for(lambda: manager.peer_count == 1)
    assert manager.piece_manager.in_flight_count("first") == 0

    second.send_piece(2)
    assert second.read_request() == 0

    second.send_piece(3)
    assert second.read_request() == 1


def test_corrupt_piece_drops_the_peer_and_is_fetched_elsewhere(
    swarm_file, fake_peer, start_server, make_manager, tmp_path
):
    count = swarm_file.metainfo.piece_count
    bad = fake_peer("bad", [True] * count)
    good = start_server("good")

    manager = make_manager(pipeline_size=1)
    manager.add_peer("127.0.0.1", bad.port)
    manager.add_peer("127.0.0.1", good.port)
    manager.start()

    assert bad.read_request() == 0
    bad.send_piece(0, corrupt=True)

    assert manager.wait_until_complete(timeout=10.0)
    assert "bad" not in manager.received_from
    _assert_file_matches(manager, swarm_file, tmp_path)


def test_unrequested_piece_drops_the_peer(swarm_file, fake_peer, make_manager):
    count = swarm_file.metainfo.piece_count
    fake = fake_peer("rude", [True] * count)

    manager = make_manager(pipeline_size=1)
    manager.add_peer("127.0.0.1", fake.port)
    manager.start()

    assert fake.read_request() == 0
    fake.send_piece(7)

    assert _wait_for(lambda: manager.peer_count == 0)
    assert not manager.piece_manager.has_piece(7)


def test_leecher_sends_have_to_connected_peers(swarm_file, fake_peer, make_manager):
    count = swarm_file.metainfo.piece_count
    fake = fake_peer("remote", [True] * count)

    manager = make_manager(pipeline_size=1)
    manager.add_peer("127.0.0.1", fake.port)
    manager.start()

    assert fake.read_request() == 0
    fake.send_piece(0)

    message_type, payload = fake.peer.receive_framed_message()

    assert message_type == MessageType.HAVE
    assert int.from_bytes(payload, "big") == 0


# ---------------------------------------------------------
# Tracker + swarm integration
# ---------------------------------------------------------

def test_download_using_tracker(
    swarm_file, start_server, tracker_server, make_manager, tmp_path
):
    client = TrackerClient("127.0.0.1", tracker_server.port)

    for n in range(3):
        server = start_server(f"seed-{n}")
        client.register(SWARM_ID, f"seed-{n}", "127.0.0.1", server.port)

    manager = make_manager()
    peers = client.get_peers(SWARM_ID, exclude="leecher")

    assert manager.connect_to_peers(peers) == 3
    manager.start()

    assert manager.wait_until_complete(timeout=10.0)
    _assert_file_matches(manager, swarm_file, tmp_path)


def test_tracker_swarms_do_not_mix(
    swarm_file, start_server, tracker_server, make_manager
):
    client = TrackerClient("127.0.0.1", tracker_server.port)

    ours = start_server("ours")
    theirs = start_server("theirs", swarm_id="other-swarm")

    client.register(SWARM_ID, "ours", "127.0.0.1", ours.port)
    client.register("other-swarm", "theirs", "127.0.0.1", theirs.port)

    peers = client.get_peers(SWARM_ID)

    assert [p["peer_id"] for p in peers] == ["ours"]
    assert make_manager().connect_to_peers(peers) == 1


def test_unregistered_peer_is_no_longer_offered(start_server, tracker_server):
    client = TrackerClient("127.0.0.1", tracker_server.port)
    server = start_server("seed")

    client.register(SWARM_ID, "seed", "127.0.0.1", server.port)
    client.unregister(SWARM_ID, "seed")

    assert client.get_peers(SWARM_ID) == []
