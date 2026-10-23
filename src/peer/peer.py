import argparse
import os
import socket
import struct
import sys

from .connection import PeerConnection, ConnectionClosed
from .protocol import (
    Handshake,
    PeerState,
    PROTOCOL_ID,
    MessageType,
    encode_handshake,
    decode_handshake,
    validate_handshake,
    encode_message,
    decode_message,
    # ---- Phase 3 payload helpers ----
    encode_bitfield,
    decode_bitfield,
    encode_have,
    decode_have,
    encode_request,
    decode_request,
    encode_piece,
    decode_piece,
)
from .pieces import verify_piece, write_piece
from ..metainfo.metainfo import load_metainfo


class Peer:

    def __init__(
        self,
        peer_id: str,
        swarm_id: str,
        metainfo=None,
        file_path: str = None,
        pieces_dir: str = None,
        have: list = None,
    ):

        self.peer_id = peer_id
        self.swarm_id = swarm_id

        self.state = PeerState.DISCONNECTED

        self.connection = None
        self.remote_peer_id = None

        # ---- Phase 3 (all None when running plain Phase 2) ----
        self.metainfo = metainfo        # Metainfo object
        self.file_path = file_path      # seeder: complete source file
        self.pieces_dir = pieces_dir    # leecher: where pieces are stored
        self.have = have                # seeder: pieces to serve (None = all)

    # ---------------------------------------------------------
    # SERVER
    # ---------------------------------------------------------

    def listen(self, host: str, port: int):

        server_socket = socket.socket(
            socket.AF_INET,
            socket.SOCK_STREAM,
        )

        server_socket.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_REUSEADDR,
            1,
        )

        server_socket.bind((host, port))
        server_socket.listen(5)

        print(f"[LISTENER] Listening on {host}:{port}")
        print(f"[PEER] ID: {self.peer_id}")
        print(f"[SWARM] ID: {self.swarm_id}")

        self.state = PeerState.CONNECTING

        try:

            client_socket, address = server_socket.accept()

            print(
                f"[CONNECTION] Accepted connection from "
                f"{address[0]}:{address[1]}"
            )

            self.connection = PeerConnection(client_socket)

            self.state = PeerState.HANDSHAKING

            self.perform_server_handshake()

            # Phase 3: runs only after a successful handshake
            if (
                self.state == PeerState.CONNECTED
                and self.metainfo is not None
            ):
                self.run_seeder()

        except KeyboardInterrupt:

            print("\n[SERVER] Shutting down")

        except Exception as exc:

            print(f"[ERROR] {exc}")

            self.state = PeerState.CLOSED

        finally:

            if self.connection:
                self.connection.close()

            server_socket.close()

            self.state = PeerState.CLOSED

            print("[PEER] Closed")

    # ---------------------------------------------------------
    # CLIENT
    # ---------------------------------------------------------

    def connect(self, host: str, port: int):

        sock = socket.socket(
            socket.AF_INET,
            socket.SOCK_STREAM,
        )

        self.state = PeerState.CONNECTING

        print(
            f"[CONNECTION] Connecting to "
            f"{host}:{port}"
        )

        try:

            sock.connect((host, port))

            print("[CONNECTION] TCP connection established")

            self.connection = PeerConnection(sock)

            self.state = PeerState.HANDSHAKING

            self.perform_client_handshake()

            # Phase 3: runs only after a successful handshake
            if (
                self.state == PeerState.CONNECTED
                and self.metainfo is not None
            ):
                self.run_leecher()

        except Exception as exc:

            print(f"[ERROR] {exc}")

            self.state = PeerState.CLOSED

        finally:

            if self.connection:
                self.connection.close()

            print("[PEER] Closed")

    # ---------------------------------------------------------
    # SERVER HANDSHAKE
    # ---------------------------------------------------------

    def perform_server_handshake(self):

        print("[STATE] HANDSHAKING")

        try:

            received = self.receive_framed_message()

            if received is None:
                raise ConnectionClosed(
                    "No handshake received"
                )

            message_type, payload = received

            if message_type != MessageType.HANDSHAKE:
                raise ValueError(
                    "Expected HANDSHAKE message"
                )

            handshake = decode_handshake(payload)

            print(
                f"[HANDSHAKE] Received from "
                f"{handshake.peer_id}"
            )

            valid = validate_handshake(
                handshake,
                PROTOCOL_ID,
                self.swarm_id,
            )

            if not valid:
                print("[HANDSHAKE] Rejected")

                return

            self.remote_peer_id = handshake.peer_id

            response = Handshake(
                protocol_id=PROTOCOL_ID,
                swarm_id=self.swarm_id,
                peer_id=self.peer_id,
            )

            self.send_framed_message(
                MessageType.HANDSHAKE,
                encode_handshake(response),
            )

            self.state = PeerState.CONNECTED

            print(
                f"[HANDSHAKE] Accepted peer "
                f"{self.remote_peer_id}"
            )

            print("[STATE] CONNECTED")

        except Exception as exc:

            print(
                f"[HANDSHAKE ERROR] {exc}"
            )

            self.state = PeerState.CLOSED

    # ---------------------------------------------------------
    # CLIENT HANDSHAKE
    # ---------------------------------------------------------

    def perform_client_handshake(self):

        print("[STATE] HANDSHAKING")

        try:

            handshake = Handshake(
                protocol_id=PROTOCOL_ID,
                swarm_id=self.swarm_id,
                peer_id=self.peer_id,
            )

            self.send_framed_message(
                MessageType.HANDSHAKE,
                encode_handshake(handshake),
            )

            print("[HANDSHAKE] Sent")

            received = self.receive_framed_message()

            if received is None:
                raise ConnectionClosed(
                    "No handshake response"
                )

            message_type, payload = received

            if message_type != MessageType.HANDSHAKE:
                raise ValueError(
                    "Expected HANDSHAKE response"
                )

            response = decode_handshake(payload)

            print(
                f"[HANDSHAKE] Received from "
                f"{response.peer_id}"
            )

            valid = validate_handshake(
                response,
                PROTOCOL_ID,
                self.swarm_id,
            )

            if not valid:
                print("[HANDSHAKE] Rejected")

                return

            self.remote_peer_id = response.peer_id

            self.state = PeerState.CONNECTED

            print(
                f"[HANDSHAKE] Accepted peer "
                f"{self.remote_peer_id}"
            )

            print("[STATE] CONNECTED")

        except Exception as exc:

            print(
                f"[HANDSHAKE ERROR] {exc}"
            )

            self.state = PeerState.CLOSED

    # ---------------------------------------------------------
    # PHASE 3: SEEDER
    # ---------------------------------------------------------

    def run_seeder(self):
        """
        BITFIELD -> wait for REQUEST -> PIECE (repeat)

        The seeder owns the complete file, so every bit is 1 unless
        `have` limits which pieces it advertises and serves.
        It keeps serving until the leecher closes the connection.
        """

        piece_count = self.metainfo.piece_count

        have = self.have if self.have is not None else [True] * piece_count

        print("[SEEDER] Sending BITFIELD")

        self.send_framed_message(
            MessageType.BITFIELD,
            encode_bitfield(have),
        )

        while True:

            try:
                received = self.receive_framed_message()
            except (ConnectionClosed, ConnectionError):
                print("[SEEDER] Leecher disconnected")
                break

            if received is None:
                break

            message_type, payload = received

            if message_type == MessageType.REQUEST:

                index = decode_request(payload, piece_count)

                print(f"[SEEDER] REQUEST for piece {index}")

                if not have[index]:
                    print(f"[SEEDER] Piece {index} not available, closing")
                    break

                data = self.read_piece_from_file(index)

                self.send_framed_message(
                    MessageType.PIECE,
                    encode_piece(index, data, piece_count),
                )

                print(f"[SEEDER] Sent PIECE {index}")

            elif message_type == MessageType.HAVE:

                index = decode_have(payload, piece_count)

                print(f"[SEEDER] Leecher now has piece {index}")

            else:

                print(f"[SEEDER] Ignoring {message_type.name}")

    def read_piece_from_file(self, index: int) -> bytes:
        """Read one piece from the complete source file."""

        offset = index * self.metainfo.piece_size
        length = self.metainfo.piece_length(index)

        with open(self.file_path, "rb") as file:
            file.seek(offset)
            data = file.read(length)

        if len(data) != length:
            raise ValueError(
                f"Source file too short for piece {index}"
            )

        return data

    # ---------------------------------------------------------
    # PHASE 3: LEECHER
    # ---------------------------------------------------------

    def run_leecher(self):
        """
        receive BITFIELD -> find missing pieces -> for each piece:
        REQUEST -> receive PIECE -> verify SHA-256 -> store -> HAVE

        Simple sequential strategy: lowest missing index first,
        one outstanding request at a time.
        """

        piece_count = self.metainfo.piece_count

        received = self.receive_framed_message()

        if received is None:
            raise ConnectionClosed("No BITFIELD received")

        message_type, payload = received

        if message_type != MessageType.BITFIELD:
            raise ValueError("Expected BITFIELD message")

        remote_has = decode_bitfield(payload, piece_count)

        print("[LEECHER] Received BITFIELD")

        # We start with nothing, so every piece the seeder has is missing.
        local_has = [False] * piece_count

        missing = [
            index
            for index in range(piece_count)
            if remote_has[index] and not local_has[index]
        ]

        print(f"[LEECHER] Missing pieces: {missing}")

        for index in missing:

            self.download_piece(index)

            local_has[index] = True

        if all(local_has):
            print("[LEECHER] All pieces downloaded")
        else:
            print("[LEECHER] Seeder does not have every piece")

    def download_piece(self, index: int):

        piece_count = self.metainfo.piece_count

        print(f"[LEECHER] Requesting piece {index}")

        self.send_framed_message(
            MessageType.REQUEST,
            encode_request(index, piece_count),
        )

        received = self.receive_framed_message()

        if received is None:
            raise ConnectionClosed("No PIECE received")

        message_type, payload = received

        if message_type != MessageType.PIECE:
            raise ValueError("Expected PIECE message")

        piece_index, data = decode_piece(payload, piece_count)

        if piece_index != index:
            raise ValueError(
                f"Requested piece {index}, received {piece_index}"
            )

        if not verify_piece(data, self.metainfo.piece_hashes[index]):
            raise ValueError(
                f"Piece {index} failed SHA-256 verification"
            )

        print(f"[LEECHER] Piece {index} verified")

        write_piece(
            self.pieces_dir,
            index,
            data,
            piece_count,
        )

        print(f"[LEECHER] Stored piece {index}")

        self.send_framed_message(
            MessageType.HAVE,
            encode_have(index, piece_count),
        )

        print(f"[LEECHER] Sent HAVE {index}")

    # ---------------------------------------------------------
    # FRAMING
    # ---------------------------------------------------------

    def send_framed_message(
        self,
        message_type: MessageType,
        payload: bytes,
    ):

        message = encode_message(
            message_type,
            payload,
        )

        self.connection.send_bytes(message)

    def receive_framed_message(self):

        header = self.connection.receive_exactly(4)

        (length,) = struct.unpack("!I", header)

        body = self.connection.receive_exactly(length)

        return decode_message(header + body)


def main():

    parser = argparse.ArgumentParser(
        description="P2P File Distribution Peer"
    )

    mode = parser.add_mutually_exclusive_group(
        required=True
    )

    mode.add_argument(
        "--listen",
        type=int,
        help="Listen on the specified TCP port",
    )

    mode.add_argument(
        "--connect",
        nargs=2,
        metavar=("HOST", "PORT"),
        help="Connect to a peer",
    )

    parser.add_argument(
        "--peer-id",
        required=True,
        help="Unique peer ID",
    )

    parser.add_argument(
        "--swarm-id",
        required=True,
        help="Swarm/file identifier",
    )

    # ---- Phase 3 (optional: omit them and Phase 2 runs unchanged) ----

    parser.add_argument(
        "--metainfo",
        help="Path to the metainfo JSON (enables piece exchange)",
    )

    parser.add_argument(
        "--file",
        help="Seeder (--listen): complete source file to serve",
    )

    parser.add_argument(
        "--pieces-dir",
        default="pieces",
        help="Leecher (--connect): directory to store downloaded pieces",
    )

    args = parser.parse_args()

    metainfo = None

    if args.metainfo:

        metainfo = load_metainfo(args.metainfo)

        if args.listen is not None:

            if not args.file:
                parser.error("--file is required when seeding with --metainfo")

            if not os.path.isfile(args.file):
                parser.error(f"file not found: {args.file}")

            if os.path.getsize(args.file) != metainfo.file_size:
                parser.error("--file size does not match the metainfo")

    peer = Peer(
        peer_id=args.peer_id,
        swarm_id=args.swarm_id,
        metainfo=metainfo,
        file_path=args.file,
        pieces_dir=args.pieces_dir,
    )

    if args.listen is not None:

        peer.listen(
            "127.0.0.1",
            args.listen,
        )

    elif args.connect:

        host = args.connect[0]
        port = int(args.connect[1])

        peer.connect(
            host,
            port,
        )


if __name__ == "__main__":
    main()