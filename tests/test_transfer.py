import os
import socket
import threading
import time

from src.metainfo.metainfo import create_metainfo
from src.peer.peer import Peer
from src.peer.pieces import assemble_pieces


def _get_free_port():
    """Return an available localhost TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_condition(condition, timeout=10.0, interval=0.05):
    """
    Wait until condition() becomes True.

    Uses a bounded timeout instead of an arbitrary long sleep.
    """
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if condition():
            return

        time.sleep(interval)

    raise TimeoutError("Timed out waiting for transfer to complete")


def test_end_to_end_file_transfer(tmp_path):
    """
    Phase 3 end-to-end test.

    Flow:

        source file
            ↓
        metainfo + piece hashes
            ↓
        seeder
            ↓
        leecher
            ↓
        TCP connection
            ↓
        HANDSHAKE
            ↓
        BITFIELD
            ↓
        REQUEST
            ↓
        PIECE
            ↓
        SHA-256 verification
            ↓
        store piece
            ↓
        HAVE
            ↓
        repeat for all pieces
            ↓
        assemble file
            ↓
        reconstructed_bytes == original_bytes
    """

    # ---------------------------------------------------------
    # 1. Create the original source file
    # ---------------------------------------------------------

    original_bytes = (
        b"P2P FILE DISTRIBUTION PHASE 3 TEST DATA\n"
        b"This file is intentionally larger than one piece "
        b"so that multiple piece transfers are exercised.\n"
    ) * 30

    source_file = tmp_path / "source.bin"
    source_file.write_bytes(original_bytes)

    assert source_file.read_bytes() == original_bytes

    # ---------------------------------------------------------
    # 2. Create metainfo and per-piece SHA-256 hashes
    # ---------------------------------------------------------

    piece_size = 128

    metainfo = create_metainfo(
        str(source_file),
        piece_size=piece_size,
    )

    assert metainfo.file_size == len(original_bytes)
    assert metainfo.piece_size == piece_size
    assert metainfo.piece_count > 1
    assert len(metainfo.piece_hashes) == metainfo.piece_count

    # ---------------------------------------------------------
    # 3. Prepare directories
    # ---------------------------------------------------------

    pieces_dir = tmp_path / "leecher_pieces"
    pieces_dir.mkdir()

    reconstructed_file = tmp_path / "reconstructed.bin"

    # ---------------------------------------------------------
    # 4. Create seeder and leecher
    # ---------------------------------------------------------

    swarm_id = "phase3-test-swarm"

    seeder = Peer(
        peer_id="peer-seeder",
        swarm_id=swarm_id,
        metainfo=metainfo,
        file_path=str(source_file),
    )

    leecher = Peer(
        peer_id="peer-leecher",
        swarm_id=swarm_id,
        metainfo=metainfo,
        pieces_dir=str(pieces_dir),
    )

    # ---------------------------------------------------------
    # 5. Start the seeder
    # ---------------------------------------------------------

    port = _get_free_port()

    seeder_thread = threading.Thread(
        target=seeder.listen,
        args=("127.0.0.1", port),
        daemon=True,
    )

    seeder_thread.start()

    # Wait until the seeder has entered its listening/connection state.
    _wait_for_condition(
        lambda: seeder.state.value in {
            "connecting",
            "handshaking",
            "connected",
        }
    )

    # ---------------------------------------------------------
    # 6. Start the leecher
    # ---------------------------------------------------------

    leecher_thread = threading.Thread(
        target=leecher.connect,
        args=("127.0.0.1", port),
        daemon=True,
    )

    leecher_thread.start()

    # ---------------------------------------------------------
    # 7. Wait for the complete piece transfer
    # ---------------------------------------------------------

    def all_pieces_received():
        if not pieces_dir.exists():
            return False

        for index in range(metainfo.piece_count):
            piece_path = pieces_dir / f"piece_{index:06d}.part"

            if not piece_path.is_file():
                return False

        return True

    _wait_for_condition(
        all_pieces_received,
        timeout=15.0,
    )

    # ---------------------------------------------------------
    # 8. Verify every received piece exists
    # ---------------------------------------------------------

    received_piece_paths = []

    for index in range(metainfo.piece_count):
        piece_path = pieces_dir / f"piece_{index:06d}.part"

        assert piece_path.is_file()
        assert piece_path.stat().st_size == metainfo.piece_length(index)

        received_piece_paths.append(piece_path)

    assert len(received_piece_paths) == metainfo.piece_count

    # ---------------------------------------------------------
    # 9. Assemble all received pieces
    # ---------------------------------------------------------

    assemble_pieces(
        str(pieces_dir),
        str(reconstructed_file),
        metainfo.piece_count,
    )

    assert reconstructed_file.is_file()

    # ---------------------------------------------------------
    # 10. Final correctness check
    # ---------------------------------------------------------

    reconstructed_bytes = reconstructed_file.read_bytes()

    assert reconstructed_bytes == original_bytes

    # ---------------------------------------------------------
    # 11. Verify the complete file size
    # ---------------------------------------------------------

    assert reconstructed_file.stat().st_size == len(original_bytes)
