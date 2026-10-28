import hashlib
import json
import os
from dataclasses import dataclass
from typing import List

DEFAULT_PIECE_SIZE = 256 * 1024
SHA256_HEX_LENGTH = 64
_HEX_DIGITS = set("0123456789abcdef")


class MetainfoError(ValueError):
    """Raised when metainfo is invalid or cannot be loaded."""


@dataclass(frozen=True)
class Metainfo:
    file_name: str
    file_size: int
    piece_size: int
    piece_count: int
    piece_hashes: List[str]

    def piece_length(self, index: int) -> int:
        """Return the byte length of piece `index` (last piece may be smaller)."""
        if not isinstance(index, int) or not 0 <= index < self.piece_count:
            raise IndexError(f"piece index out of range: {index}")

        if index == self.piece_count - 1:
            return self.file_size - index * self.piece_size

        return self.piece_size

    def to_dict(self) -> dict:
        return {
            "file_name": self.file_name,
            "file_size": self.file_size,
            "piece_size": self.piece_size,
            "piece_count": self.piece_count,
            "piece_hashes": list(self.piece_hashes),
        }


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_metainfo(data: dict) -> Metainfo:
    """Validate a metainfo dictionary and return a Metainfo object."""
    if not isinstance(data, dict):
        raise MetainfoError("metainfo must be a JSON object")

    required = (
        "file_name",
        "file_size",
        "piece_size",
        "piece_count",
        "piece_hashes",
    )

    for key in required:
        if key not in data:
            raise MetainfoError(f"missing field: {key}")

    file_name = data["file_name"]
    file_size = data["file_size"]
    piece_size = data["piece_size"]
    piece_count = data["piece_count"]
    piece_hashes = data["piece_hashes"]

    if not isinstance(file_name, str) or not file_name:
        raise MetainfoError("file_name must be a non-empty string")

    if file_name != os.path.basename(file_name) or file_name in (".", ".."):
        raise MetainfoError("file_name must not contain path components")

    if not _is_int(file_size) or file_size < 0:
        raise MetainfoError("file_size must be a non-negative integer")

    if not _is_int(piece_size) or piece_size <= 0:
        raise MetainfoError("piece_size must be a positive integer")

    if not _is_int(piece_count) or piece_count < 0:
        raise MetainfoError("piece_count must be a non-negative integer")

    expected_count = (file_size + piece_size - 1) // piece_size

    if piece_count != expected_count:
        raise MetainfoError(
            f"piece_count {piece_count} does not match "
            f"file_size/piece_size (expected {expected_count})"
        )

    if not isinstance(piece_hashes, list):
        raise MetainfoError("piece_hashes must be a list")

    if len(piece_hashes) != piece_count:
        raise MetainfoError(
            f"expected {piece_count} piece hashes, got {len(piece_hashes)}"
        )

    for index, digest in enumerate(piece_hashes):
        if (
            not isinstance(digest, str)
            or len(digest) != SHA256_HEX_LENGTH
            or not set(digest) <= _HEX_DIGITS
        ):
            raise MetainfoError(
                f"piece_hashes[{index}] is not a lowercase SHA-256 hex digest"
            )

    return Metainfo(
        file_name=file_name,
        file_size=file_size,
        piece_size=piece_size,
        piece_count=piece_count,
        piece_hashes=list(piece_hashes),
    )


def hash_piece(data: bytes) -> str:
    """Return the SHA-256 hex digest of one piece."""
    return hashlib.sha256(data).hexdigest()


def create_metainfo(
    file_path: str,
    piece_size: int = DEFAULT_PIECE_SIZE,
) -> Metainfo:
    """Read `file_path` in binary mode and build its Metainfo."""
    if not _is_int(piece_size) or piece_size <= 0:
        raise ValueError("piece_size must be a positive integer")

    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"file not found: {file_path}")

    piece_hashes = []
    file_size = 0

    with open(file_path, "rb") as file:
        while True:
            piece = file.read(piece_size)

            if not piece:
                break

            file_size += len(piece)
            piece_hashes.append(hash_piece(piece))

    return Metainfo(
        file_name=os.path.basename(file_path),
        file_size=file_size,
        piece_size=piece_size,
        piece_count=len(piece_hashes),
        piece_hashes=piece_hashes,
    )


def save_metainfo(metainfo: Metainfo, path: str) -> None:
    """Validate and write `metainfo` to `path` as readable JSON."""
    validate_metainfo(metainfo.to_dict())

    with open(path, "w", encoding="utf-8") as file:
        json.dump(metainfo.to_dict(), file, indent=4)
        file.write("\n")


def load_metainfo(path: str) -> Metainfo:
    """Load and validate metainfo from a JSON file."""
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError as exc:
        raise MetainfoError(f"invalid JSON in {path}: {exc}") from exc

    return validate_metainfo(data)
# ------------------------------------------------------------------
# Backward-compatible API used by the original test suite
# ------------------------------------------------------------------

def generate(file_path, tracker_url=None, piece_length=None, output_path=None, **kwargs):
    """Compatibility wrapper around create_metainfo/save_metainfo."""
    piece_size = piece_length if piece_length is not None else kwargs.get(
        "piece_size", 256 * 1024
    )

    meta = create_metainfo(file_path, piece_size=piece_size)

    if output_path is not None:
        # Save with tracker URL in the metadata
        data = meta.to_dict()
        if tracker_url:
            data['announce'] = tracker_url
        
        import json
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
            f.write("\n")

    return meta


def parse(path):
    """Compatibility wrapper for loading metainfo from a file."""
    meta = load_metainfo(path)
    # Return dict format expected by client code
    import hashlib
    info_dict = meta.to_dict()
    info_hash = hashlib.sha256(str(info_dict).encode()).digest()
    
    return {
        'file_name': meta.file_name,
        'file_length': meta.file_size,
        'piece_length': meta.piece_size,
        'piece_hashes': [bytes.fromhex(h) for h in meta.piece_hashes],
        'info_hash': info_hash,
        'info_hash_hex': info_hash.hex(),
        'tracker_url': 'http://127.0.0.1:8000',  # default, can be overridden
    }


def parse_bytes(data):
    """Compatibility parser for bencoded metainfo bytes."""
    from . import bencode

    decoded = bencode.decode(data)
    return validate_metainfo(decoded)
