import socket
import threading

from ..peer.connection import ConnectionClosed, PeerConnection
from ..peer.peer import Peer
from ..peer.protocol import PeerState


class PeerServer:
    """
    Accepts many leecher connections and serves pieces to each of them.

    Every connection gets its own Peer, so the existing handshake,
    framing and seeder loop are reused. `have` limits which pieces are
    advertised and served (default: all of them).
    """

    def __init__(self, peer_id, swarm_id, metainfo, file_path, have=None):
        if have is not None and len(have) != metainfo.piece_count:
            raise ValueError("have must contain one flag per piece")

        self.peer_id = peer_id
        self.swarm_id = swarm_id
        self.metainfo = metainfo
        self.file_path = file_path
        self.have = have

        self._server = None
        self._thread = None
        self._stopped = threading.Event()
        self._connections = []

    def start(self, host="127.0.0.1", port=0):
        """Start listening in a background thread. Returns the port."""
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((host, port))
        self._server.listen(16)
        self._server.settimeout(0.2)

        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

        return self._server.getsockname()[1]

    def stop(self):
        self._stopped.set()

        if self._thread:
            self._thread.join()

        if self._server:
            self._server.close()

        for sock in self._connections:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

            sock.close()

    def _accept_loop(self):
        while not self._stopped.is_set():
            try:
                sock, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            sock.settimeout(None)
            self._connections.append(sock)

            threading.Thread(
                target=self._serve,
                args=(sock,),
                daemon=True,
            ).start()

    def _serve(self, sock):
        peer = Peer(
            self.peer_id,
            self.swarm_id,
            self.metainfo,
            file_path=self.file_path,
            have=self.have,
        )

        peer.connection = PeerConnection(sock)
        peer.state = PeerState.HANDSHAKING

        try:
            peer.perform_server_handshake()

            if peer.state == PeerState.CONNECTED:
                peer.run_seeder()

        except (OSError, ConnectionClosed, ValueError) as exc:
            print(f"[SERVER] Connection error: {exc}")

        finally:
            peer.connection.close()
