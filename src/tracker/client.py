import hashlib
import json
import re
import socket
import uuid
from urllib.parse import urlencode


class TrackerError(Exception):
    """Raised when the tracker is unreachable or rejects a request."""


class TrackerClient:
    """
    Client for the HTTP tracker server.

    The public API intentionally stays compatible with the existing
    PeerManager/tests:
        register(...)
        get_peers(...)
        unregister(...)
    """

    def __init__(self, host, port, timeout=5.0):
        self.host = host
        self.port = int(port)
        self.timeout = timeout

    def register(self, swarm_id, peer_id, host, port):
        """
        Register a peer with the tracker.

        `swarm_id` is used as the tracker's info_hash identifier.
        """

        reply = self._announce(
            info_hash=swarm_id,
            peer_id=peer_id,
            port=port,
            event="started",
        )

        return reply

    def get_peers(self, swarm_id, exclude=None):
        """
        Return:
            [{'peer_id': ..., 'host': ..., 'port': ...}, ...]

        The tracker returns the peer list from /announce.
        """

        # The tracker has no read-only "list peers" call: /announce needs a
        # peer_id and registers whoever asks. Look up under a throwaway id
        # (so the caller is never registered, and `exclude` is not touched),
        # then remove that id again.
        probe_id = "query-" + uuid.uuid4().hex

        try:
            reply = self._announce(
                info_hash=swarm_id,
                peer_id=probe_id,
                port=1,
                event="",
            )
        finally:
            try:
                self._announce(
                    info_hash=swarm_id,
                    peer_id=probe_id,
                    port=1,
                    event="stopped",
                )
            except TrackerError:
                pass

        peers = reply.get("peers", [])
        if not isinstance(peers, list):
            raise TrackerError("invalid tracker peer list")

        peers = [
            self._peer_for_manager(peer)
            for peer in peers
        ]

        if exclude:
            peers = [
                peer for peer in peers
                if peer.get("peer_id") != exclude
            ]

        return peers

    @staticmethod
    def _peer_for_manager(peer):
        if not isinstance(peer, dict):
            raise TrackerError("invalid peer in tracker response")

        try:
            peer_id = peer["peer_id"]
            host = peer["host"] if "host" in peer else peer["ip"]
            port = peer["port"]
        except (KeyError, TypeError) as exc:
            raise TrackerError("invalid peer in tracker response") from exc

        return {
            "peer_id": peer_id,
            "host": host,
            "port": port,
        }

    def unregister(self, swarm_id, peer_id):
        """
        Tell the tracker that this peer has stopped.

        The HTTP tracker represents this as event=stopped.
        """

        reply = self._announce(
            info_hash=swarm_id,
            peer_id=peer_id,
            port=1,
            event="stopped",
        )

        return True

    def _announce(self, info_hash, peer_id, port, event=""):
        """
        Send an HTTP GET /announce request using the standard library.

        We intentionally use a small socket implementation instead of
        urllib so the client remains lightweight and works with the
        project's simple asyncio HTTP tracker.
        """

        if isinstance(info_hash, bytes):
            info_hash = info_hash.hex()
        else:
            info_hash = str(info_hash)

            # The tracker needs a 64-character hex info_hash; logical swarm
            # IDs map deterministically to SHA-256 so register/get_peers/
            # unregister always agree.
            if not re.fullmatch(r"[0-9a-fA-F]{64}", info_hash):
                info_hash = hashlib.sha256(
                    info_hash.encode("utf-8")
                ).hexdigest()

        params = {
            "info_hash": info_hash,
            "peer_id": str(peer_id),
            "port": int(port),
            "event": event,
        }

        query = urlencode(params)
        request = (
            f"GET /announce?{query} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode("ascii")

        try:
            with socket.create_connection(
                (self.host, self.port),
                timeout=self.timeout,
            ) as sock:
                sock.sendall(request)

                chunks = []
                while True:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)

        except (OSError, TypeError, ValueError) as exc:
            raise TrackerError(
                f"tracker unreachable: {exc}"
            ) from exc

        raw = b"".join(chunks)

        if not raw:
            raise TrackerError(
                "tracker closed the connection without a reply"
            )

        try:
            header, body = raw.split(b"\r\n\r\n", 1)
        except ValueError as exc:
            raise TrackerError(
                "invalid tracker HTTP response"
            ) from exc

        try:
            status_line = header.split(b"\r\n", 1)[0]
            status = int(status_line.split()[1])
        except (ValueError, IndexError) as exc:
            raise TrackerError(
                "invalid tracker HTTP status"
            ) from exc

        try:
            reply = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise TrackerError(
                f"invalid tracker reply: {exc}"
            ) from exc

        if status != 200:
            raise TrackerError(
                reply.get("failure reason", "tracker request rejected")
            )

        if not isinstance(reply, dict):
            raise TrackerError("invalid tracker response")

        if "failure reason" in reply:
            raise TrackerError(reply["failure reason"])

        return reply
