"""
metainfo.py - create and read .torrent style metainfo files

Layout (bencoded dict):

    tracker_url    str   e.g. http://127.0.0.1:8000
    created_by     str
    creation_date  int   unix time
    info           dict
        file_name     str
        file_length   int    bytes
        piece_length  int    bytes
        pieces        bytes  num_pieces * 32, the piece sha256 hashes back to back
        file_hash     bytes  sha256 of the complete file (32 bytes)

info_hash = sha256(bencode(info)). It identifies the swarm, peers send it
in the handshake and the tracker keys swarms by it. Only the info dict
goes into it, so changing the tracker url or creation date does not
create a "different" torrent.
"""

import hashlib
import logging
import os
import time

from . import bencode
from .file_splitter import split_file, num_pieces_for, HASH_LEN, DEFAULT_PIECE_LENGTH

log = logging.getLogger('metainfo')

CREATED_BY = 'bittorrent-lite'


class MetainfoError(ValueError):
    pass


def compute_info_hash(info_dict):
    return hashlib.sha256(bencode.encode(info_dict)).digest()


def generate(filepath, tracker_url, piece_length=DEFAULT_PIECE_LENGTH, output_path=None):
    """
    Build a .torrent file for `filepath`.

    Returns (output_path, info_hash_hex)
    """
    if isinstance(tracker_url, bytes):
        tracker_url = tracker_url.decode('utf-8')
    if not tracker_url.startswith('http://'):
        # the tracker only speaks plain http over raw sockets, no https
        raise MetainfoError(f"tracker url must start with http:// (got {tracker_url!r})")

    split = split_file(filepath, piece_length)

    info = {
        b'file_name': split['file_name'].encode('utf-8'),
        b'file_length': split['file_length'],
        b'piece_length': split['piece_length'],
        b'pieces': b''.join(split['piece_hashes']),
        b'file_hash': split['file_hash'],
    }
    info_hash = compute_info_hash(info)

    meta = {
        b'tracker_url': tracker_url.encode('utf-8'),
        b'created_by': CREATED_BY.encode(),
        b'creation_date': int(time.time()),
        b'info': info,
    }

    if output_path is None:
        output_path = os.path.splitext(filepath)[0] + '.torrent'

    with open(output_path, 'wb') as f:
        f.write(bencode.encode(meta))

    log.info("created %s: %s, %d bytes, %d pieces of %d, info_hash %s",
             output_path, split['file_name'], split['file_length'],
             split['num_pieces'], piece_length, info_hash.hex())

    return output_path, info_hash.hex()


def parse(torrent_path):
    """
    Read a .torrent file. Returns a dict:

        tracker_url, file_name, file_length, piece_length, num_pieces,
        piece_hashes (list of 32 byte digests), file_hash (bytes or None),
        info_hash (32 bytes), info_hash_hex, created_by, creation_date,
        raw_info (the decoded info dict)
    """
    with open(torrent_path, 'rb') as f:
        raw = f.read()
    return parse_bytes(raw)


def parse_bytes(raw):
    try:
        meta = bencode.decode(raw)
    except bencode.BencodeError as e:
        raise MetainfoError(f"not a valid metainfo file: {e}") from None

    if not isinstance(meta, dict):
        raise MetainfoError("top level must be a dict")

    info = _require(meta, b'info', dict)
    tracker_url = _require(meta, b'tracker_url', bytes).decode('utf-8')

    file_name = _require(info, b'file_name', bytes).decode('utf-8')
    file_length = _require(info, b'file_length', int)
    piece_length = _require(info, b'piece_length', int)
    pieces_blob = _require(info, b'pieces', bytes)

    # a file name like ../../x would let a malicious torrent write outside download_dir
    if not file_name or file_name != os.path.basename(file_name) or file_name in ('.', '..'):
        raise MetainfoError(f"bad file_name {file_name!r}")
    if file_length <= 0:
        raise MetainfoError(f"bad file_length {file_length}")
    if piece_length <= 0:
        raise MetainfoError(f"bad piece_length {piece_length}")
    if len(pieces_blob) % HASH_LEN != 0:
        raise MetainfoError(f"pieces length {len(pieces_blob)} is not a multiple of {HASH_LEN}")

    num_pieces = len(pieces_blob) // HASH_LEN
    expected = num_pieces_for(file_length, piece_length)
    if num_pieces != expected:
        raise MetainfoError(
            f"{num_pieces} piece hashes but file_length/piece_length needs {expected}")

    piece_hashes = [pieces_blob[i:i + HASH_LEN] for i in range(0, len(pieces_blob), HASH_LEN)]

    # older torrents made before we added file_hash won't have it
    file_hash = info.get(b'file_hash')
    if file_hash is not None and (not isinstance(file_hash, bytes) or len(file_hash) != HASH_LEN):
        raise MetainfoError("file_hash must be 32 bytes")

    info_hash = compute_info_hash(info)

    created_by = meta.get(b'created_by', b'')
    return {
        'tracker_url': tracker_url,
        'file_name': file_name,
        'file_length': file_length,
        'piece_length': piece_length,
        'num_pieces': num_pieces,
        'piece_hashes': piece_hashes,
        'file_hash': file_hash,
        'info_hash': info_hash,
        'info_hash_hex': info_hash.hex(),
        'created_by': created_by.decode('utf-8', 'replace') if isinstance(created_by, bytes) else '',
        'creation_date': meta.get(b'creation_date'),
        'raw_info': info,
    }


def _require(d, key, typ):
    if key not in d:
        raise MetainfoError(f"missing field {key.decode()}")
    val = d[key]
    if not isinstance(val, typ):
        raise MetainfoError(f"field {key.decode()} should be {typ.__name__}, got {type(val).__name__}")
    return val
