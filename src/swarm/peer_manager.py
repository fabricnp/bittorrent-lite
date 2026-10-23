import socket
import threading

from ..peer.connection import ConnectionClosed, PeerConnection
from ..peer.peer import Peer
from ..peer.pieces import verify_piece, write_piece
from ..peer.protocol import (
    MessageType,
    PeerState,
    decode_bitfield,
    decode_have,
    decode_piece,
    encode_have,
    encode_request,
)
from .piece_manager import PieceManager
from .scheduler import select_rarest_piece

DEFAULT_PIPELINE_SIZE = 5
DEFAULT_MAX_PEERS = 6
CONNECT_TIMEOUT = 5.0


class PeerManager:
    """
    Leecher that downloads one file from several peers at once.

    Each connected peer is a Peer object (existing handshake and framing)
    with its own reader thread. All shared state is guarded by one lock.
    Up to `pipeline_size` REQUESTs are kept outstanding per peer, and the
    piece for each request comes from the rarest-first scheduler.
    """

    def __init__(
        self,
        peer_id,
        swarm_id,
        metainfo,
        pieces_dir,
        pipeline_size=DEFAULT_PIPELINE_SIZE,
        max_peers=DEFAULT_MAX_PEERS,
    ):
        if pipeline_size < 1:
            raise ValueError("pipeline_size must be at least 1")

        if max_peers < 1:
            raise ValueError("max_peers must be at least 1")

        self.peer_id = peer_id
        self.swarm_id = swarm_id
        self.metainfo = metainfo
        self.pieces_dir = pieces_dir
        self.pipeline_size = pipeline_size
        self.max_peers = max_peers

        self.piece_manager = PieceManager(metainfo.piece_count)
        self.received_from = {}

        self._peers = {}
        self._started = False
        self._lock = threading.RLock()
        self._finished = threading.Event()

        if self.piece_manager.is_complete():
            self._finished.set()

    @property
    def peer_count(self):
        with self._lock:
            return len(self._peers)

    # ---------------------------------------------------------
    # CONNECTING
    # ---------------------------------------------------------

    def connect_to_peers(self, peers):
        """
        Connect to peers given as {'host', 'port'} dicts (the tracker's
        format) until max_peers is reached. Returns the number connected.
        """
        connected = 0

        for entry in peers:
            if self.peer_count >= self.max_peers:
                break

            if self.add_peer(entry["host"], entry["port"]):
                connected += 1

        return connected

    def add_peer(self, host, port):
        """Connect, handshake and read the BITFIELD. Returns True on success."""
        if self.peer_count >= self.max_peers:
            return False

        result = self._handshake(host, port)

        if result is None:
            return False

        peer, bitfield = result
        remote_id = peer.remote_peer_id

        with self._lock:
            if (
                len(self._peers) >= self.max_peers
                or remote_id == self.peer_id
                or remote_id in self._peers
            ):
                peer.connection.close()
                return False

            self._peers[remote_id] = peer
            self.piece_manager.add_peer(remote_id, bitfield)
            self._finished.clear()

        threading.Thread(
            target=self._reader,
            args=(peer,),
            daemon=True,
        ).start()

        self._fill(peer)

        return True

    def _handshake(self, host, port):
        try:
            sock = socket.create_connection(
                (host, port),
                timeout=CONNECT_TIMEOUT,
            )
        except OSError as exc:
            print(f"[MANAGER] Cannot connect to {host}:{port}: {exc}")
            return None

        peer = Peer(
            self.peer_id,
            self.swarm_id,
            self.metainfo,
            pieces_dir=self.pieces_dir,
        )

        peer.connection = PeerConnection(sock)
        peer.state = PeerState.HANDSHAKING
        peer.perform_client_handshake()

        try:
            if peer.state != PeerState.CONNECTED:
                raise ConnectionClosed("handshake failed")

            message_type, payload = peer.receive_framed_message()

            if message_type != MessageType.BITFIELD:
                raise ValueError("expected BITFIELD after handshake")

            bitfield = decode_bitfield(payload, self.metainfo.piece_count)

            sock.settimeout(None)

        except (OSError, ConnectionClosed, ValueError) as exc:
            print(f"[MANAGER] Peer {host}:{port} rejected: {exc}")
            peer.connection.close()
            return None

        return peer, bitfield

    # ---------------------------------------------------------
    # DOWNLOADING
    # ---------------------------------------------------------

    def start(self):
        """
        Begin requesting pieces. Call after connecting so that the
        scheduler sees every peer's BITFIELD before its first choice.
        """
        with self._lock:
            self._started = True
            peers = list(self._peers.values())

        for peer in peers:
            self._fill(peer)

    def wait_until_complete(self, timeout=None):
        """
        Block until every piece is downloaded, all peers are gone, or the
        timeout expires. Returns True only if the download is complete.
        """
        self._finished.wait(timeout)

        return self.piece_manager.is_complete()

    def close(self):
        with self._lock:
            peers = list(self._peers.values())

        for peer in peers:
            peer.connection.close()

    def _fill(self, peer):
        """Top up the peer's pipeline with rarest-first REQUESTs."""
        with self._lock:
            remote_id = peer.remote_peer_id

            if not self._started or self._peers.get(remote_id) is not peer:
                return

            while self.piece_manager.in_flight_count(remote_id) < self.pipeline_size:
                index = select_rarest_piece(self.piece_manager, remote_id)

                if index is None:
                    break

                self.piece_manager.mark_in_flight(index, remote_id)

                try:
                    peer.send_framed_message(
                        MessageType.REQUEST,
                        encode_request(index, self.metainfo.piece_count),
                    )
                except OSError:
                    break

                print(f"[MANAGER] REQUEST piece {index} from {remote_id}")

    def _reader(self, peer):
        try:
            while True:
                message_type, payload = peer.receive_framed_message()

                if message_type == MessageType.HAVE:
                    self._on_have(peer, payload)

                elif message_type == MessageType.PIECE:
                    self._on_piece(peer, payload)

        except (OSError, ConnectionClosed, ValueError) as exc:
            print(f"[MANAGER] Peer {peer.remote_peer_id} dropped: {exc}")

        finally:
            self._drop_peer(peer)

    def _on_have(self, peer, payload):
        index = decode_have(payload, self.metainfo.piece_count)

        with self._lock:
            self.piece_manager.update_peer_have(peer.remote_peer_id, index)

        self._fill(peer)

    def _on_piece(self, peer, payload):
        piece_count = self.metainfo.piece_count
        remote_id = peer.remote_peer_id

        index, data = decode_piece(payload, piece_count)

        with self._lock:
            if self.piece_manager.in_flight_owner(index) != remote_id:
                raise ValueError(f"unrequested piece {index}")

        if not verify_piece(data, self.metainfo.piece_hashes[index]):
            raise ValueError(f"piece {index} failed SHA-256 verification")

        write_piece(self.pieces_dir, index, data, piece_count)

        print(f"[MANAGER] Piece {index} verified from {remote_id}")

        with self._lock:
            self.piece_manager.mark_downloaded(index)
            self.received_from[remote_id] = self.received_from.get(remote_id, 0) + 1

            for other in list(self._peers.values()):
                try:
                    other.send_framed_message(
                        MessageType.HAVE,
                        encode_have(index, piece_count),
                    )
                except OSError:
                    pass

            if self.piece_manager.is_complete():
                self._finished.set()
                return

        self._fill(peer)

    def _drop_peer(self, peer):
        """Remove a peer and hand its in-flight pieces to the others."""
        with self._lock:
            remote_id = peer.remote_peer_id

            if self._peers.get(remote_id) is peer:
                del self._peers[remote_id]
                self.piece_manager.remove_peer(remote_id)

                if not self._peers:
                    self._finished.set()

            remaining = list(self._peers.values())

        peer.connection.close()

        for other in remaining:
            self._fill(other)
