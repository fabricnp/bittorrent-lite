"""
file_splitter.py - split a file into fixed size pieces and hash them

Every piece is piece_length bytes except the last one, which holds
whatever is left over. Each piece gets its own SHA-256 so a peer can
check a piece the moment it arrives instead of waiting for the whole
file.

While we're reading the file anyway we also hash the whole thing, that
digest goes into the metainfo and is used for the final end-to-end check
after reassembly.
"""

import hashlib
import os

DEFAULT_PIECE_LENGTH = 256 * 1024
HASH_LEN = 32  # sha256 digest size


def check_piece_length(piece_length):
    if not isinstance(piece_length, int) or isinstance(piece_length, bool):
        raise TypeError("piece_length must be an int")
    if piece_length <= 0:
        raise ValueError("piece_length must be positive")


def num_pieces_for(file_length, piece_length):
    """ceil(file_length / piece_length) without going through floats"""
    return (file_length + piece_length - 1) // piece_length


def piece_size(index, file_length, piece_length):
    """Size in bytes of piece `index`. Only the last piece can be short."""
    n = num_pieces_for(file_length, piece_length)
    if index < 0 or index >= n:
        raise IndexError(f"piece {index} out of range (0..{n - 1})")
    if index == n - 1:
        return file_length - piece_length * (n - 1)
    return piece_length


def split_file(filepath, piece_length=DEFAULT_PIECE_LENGTH):
    """
    Read the file once, hash every piece and the full file.

    Returns a dict:
        piece_hashes  list of 32 byte sha256 digests
        piece_length  piece size used
        file_length   total size in bytes
        num_pieces
        file_name     basename of the file
        file_hash     sha256 of the whole file (32 bytes)
    """
    check_piece_length(piece_length)

    file_size = os.path.getsize(filepath)
    if file_size == 0:
        raise ValueError(f"file is empty: {filepath}")

    piece_hashes = []
    whole = hashlib.sha256()
    read_total = 0

    with open(filepath, 'rb') as f:
        while True:
            chunk = _read_exact(f, piece_length)
            if not chunk:
                break
            piece_hashes.append(hashlib.sha256(chunk).digest())
            whole.update(chunk)
            read_total += len(chunk)

    # file changed size while we were reading it -> hashes are garbage
    if read_total != file_size:
        raise IOError(f"{filepath} changed while hashing ({file_size} -> {read_total} bytes)")

    expected = num_pieces_for(file_size, piece_length)
    if len(piece_hashes) != expected:
        raise IOError(f"expected {expected} pieces, got {len(piece_hashes)}")

    return {
        'piece_hashes': piece_hashes,
        'piece_length': piece_length,
        'file_length': file_size,
        'num_pieces': expected,
        'file_name': os.path.basename(filepath),
        'file_hash': whole.digest(),
    }


def _read_exact(f, n):
    """f.read(n) can return less than n on some streams, loop until we have n or EOF"""
    buf = bytearray()
    while len(buf) < n:
        part = f.read(n - len(buf))
        if not part:
            break
        buf += part
    return bytes(buf)


def get_piece_data(filepath, piece_index, piece_length=DEFAULT_PIECE_LENGTH):
    """Read one piece straight from the source file. Last piece may be shorter."""
    if piece_index < 0:
        raise IndexError(f"negative piece index {piece_index}")

    file_size = os.path.getsize(filepath)
    offset = piece_index * piece_length
    if offset >= file_size:
        raise IndexError(f"piece {piece_index} is out of range (file is {file_size} bytes)")

    with open(filepath, 'rb') as f:
        f.seek(offset)
        return _read_exact(f, piece_length)


def verify_piece(data, expected_hash):
    """True if sha256(data) matches expected_hash."""
    return hashlib.sha256(data).digest() == expected_hash


def compute_file_hash(filepath, block_size=1024 * 1024):
    """SHA-256 of a whole file, read in 1 MB blocks so big files don't eat RAM."""
    h = hashlib.sha256()
    with open(filepath, 'rb') as f:
        while True:
            block = f.read(block_size)
            if not block:
                break
            h.update(block)
    return h.digest()
