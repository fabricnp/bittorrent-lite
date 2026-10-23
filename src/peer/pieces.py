import hashlib
import os
import shutil
from typing import Iterator, List, Optional, Tuple

SHA256_HEX_LENGTH = 64
_HEX_DIGITS = set("0123456789abcdef")


class MissingPiecesError(Exception):
    """Raised when assembling a file while some pieces are missing."""

    def __init__(self, missing: List[int]):
        self.missing = missing
        super().__init__(f"missing pieces: {missing}")


def _check_piece_size(piece_size: int) -> None:
    if not isinstance(piece_size, int) or isinstance(piece_size, bool):
        raise TypeError("piece_size must be an int")

    if piece_size <= 0:
        raise ValueError("piece_size must be positive")


def _check_index(index: int, piece_count: Optional[int] = None) -> None:
    if not isinstance(index, int) or isinstance(index, bool):
        raise TypeError("piece index must be an int")

    if index < 0:
        raise ValueError(f"invalid piece index: {index}")

    if piece_count is not None and index >= piece_count:
        raise ValueError(
            f"invalid piece index: {index} (piece_count is {piece_count})"
        )


def _piece_path(pieces_dir: str, index: int) -> str:
    return os.path.join(pieces_dir, f"piece_{index:06d}.part")


def split_file_into_pieces(
    file_path: str,
    piece_size: int,
) -> Iterator[Tuple[int, bytes]]:
    """
    Yield (index, data) for each piece of a file, starting at index 0.

    Only one piece is held in memory at a time. The final piece may be
    smaller than piece_size.
    """
    _check_piece_size(piece_size)

    with open(file_path, "rb") as file:
        index = 0

        while True:
            data = file.read(piece_size)

            if not data:
                break

            yield index, data
            index += 1


def hash_piece(data: bytes) -> str:
    """Return the SHA-256 hex digest of one piece."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("data must be bytes")

    return hashlib.sha256(data).hexdigest()


def hash_file_pieces(file_path: str, piece_size: int) -> List[str]:
    """Return the SHA-256 hex digest of every piece, in index order."""
    return [
        hash_piece(data)
        for _, data in split_file_into_pieces(file_path, piece_size)
    ]


def verify_piece(data: bytes, expected_hash: str) -> bool:
    """
    Check a piece against its expected SHA-256 hex digest.

    Returns True if it matches and False if it does not.
    Raises ValueError if expected_hash is not a valid SHA-256 hex digest.
    """
    if (
        not isinstance(expected_hash, str)
        or len(expected_hash) != SHA256_HEX_LENGTH
        or not set(expected_hash.lower()) <= _HEX_DIGITS
    ):
        raise ValueError("expected_hash is not a valid SHA-256 hex digest")

    return hash_piece(data) == expected_hash.lower()


def write_piece(
    pieces_dir: str,
    index: int,
    data: bytes,
    piece_count: Optional[int] = None,
) -> str:
    """
    Store one piece as its own file inside pieces_dir.

    Returns the path written. If piece_count is given, the index must be
    smaller than it.
    """
    _check_index(index, piece_count)

    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("data must be bytes")

    os.makedirs(pieces_dir, exist_ok=True)

    path = _piece_path(pieces_dir, index)
    temp_path = path + ".tmp"

    with open(temp_path, "wb") as file:
        file.write(data)

    os.replace(temp_path, path)

    return path


def assemble_pieces(
    pieces_dir: str,
    output_path: str,
    piece_count: int,
) -> None:
    """
    Join pieces 0 .. piece_count-1 from pieces_dir into output_path.

    Raises MissingPiecesError (and writes nothing) if any piece is absent.
    """
    if not isinstance(piece_count, int) or isinstance(piece_count, bool):
        raise TypeError("piece_count must be an int")

    if piece_count < 0:
        raise ValueError("piece_count cannot be negative")

    missing = [
        index
        for index in range(piece_count)
        if not os.path.isfile(_piece_path(pieces_dir, index))
    ]

    if missing:
        raise MissingPiecesError(missing)

    temp_path = output_path + ".tmp"

    with open(temp_path, "wb") as output:
        for index in range(piece_count):
            with open(_piece_path(pieces_dir, index), "rb") as piece:
                shutil.copyfileobj(piece, output)

    os.replace(temp_path, output_path)