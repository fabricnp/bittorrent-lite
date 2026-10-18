"""
protocol.py - Wire protocol message definitions and serialization.

Message format (after handshake):
  <4-byte length prefix><1-byte message ID><payload>

Length prefix is big-endian uint32, does NOT include itself.
Keep-alive is a 0-length message (just 4 zero bytes).

Message IDs:
  0 = choke
  1 = unchoke
  2 = interested
  3 = not_interested
  4 = have
  5 = bitfield
  6 = request
  7 = piece
  8 = cancel
"""

import struct
import hashlib

# message type IDs
MSG_CHOKE = 0
MSG_UNCHOKE = 1
MSG_INTERESTED = 2
MSG_NOT_INTERESTED = 3
MSG_HAVE = 4
MSG_BITFIELD = 5
MSG_REQUEST = 6
MSG_PIECE = 7
MSG_CANCEL = 8

# human readable names for logging
MSG_NAMES = {
    0: 'choke', 1: 'unchoke', 2: 'interested', 3: 'not_interested',
    4: 'have', 5: 'bitfield', 6: 'request', 7: 'piece', 8: 'cancel',
}

# handshake: <1 byte pstrlen><pstr><8 reserved bytes><32 byte info_hash><32 byte peer_id>
PROTOCOL_STRING = b'BitTorrent-lite'
HANDSHAKE_LEN = 1 + len(PROTOCOL_STRING) + 8 + 32 + 32  # = 88 bytes


def build_handshake(info_hash, peer_id):
    """
    Build a handshake message.

    info_hash: 32 bytes (SHA-256 of the info dict)
    peer_id: string or bytes, will be padded/truncated to 32 bytes
    """
    if isinstance(peer_id, str):
        peer_id = peer_id.encode('utf-8')
    # pad or truncate peer_id to 32 bytes
    peer_id = peer_id[:32].ljust(32, b'\x00')

    if isinstance(info_hash, str):
        info_hash = bytes.fromhex(info_hash)
    assert len(info_hash) == 32, f"info_hash must be 32 bytes, got {len(info_hash)}"

    pstr = PROTOCOL_STRING
    pstrlen = struct.pack('!B', len(pstr))
    reserved = b'\x00' * 8

    return pstrlen + pstr + reserved + info_hash + peer_id


def parse_handshake(data):
    """
    Parse a handshake message.
    Returns (info_hash, peer_id) or raises ValueError.
    """
    if len(data) < HANDSHAKE_LEN:
        raise ValueError(f"Handshake too short: {len(data)} < {HANDSHAKE_LEN}")

    pstrlen = data[0]
    pstr = data[1:1+pstrlen]

    if pstr != PROTOCOL_STRING:
        raise ValueError(f"Wrong protocol string: {pstr}")

    offset = 1 + pstrlen + 8  # skip reserved
    info_hash = data[offset:offset+32]
    peer_id = data[offset+32:offset+64]

    # strip null padding from peer_id
    peer_id = peer_id.rstrip(b'\x00')

    return info_hash, peer_id


def build_message(msg_id, payload=b''):
    """Build a wire protocol message with length prefix."""
    length = 1 + len(payload)  # 1 for msg_id
    return struct.pack('!I', length) + struct.pack('!B', msg_id) + payload


def build_keepalive():
    """Build a keep-alive message (zero-length)."""
    return struct.pack('!I', 0)


def build_choke():
    return build_message(MSG_CHOKE)


def build_unchoke():
    return build_message(MSG_UNCHOKE)


def build_interested():
    return build_message(MSG_INTERESTED)


def build_not_interested():
    return build_message(MSG_NOT_INTERESTED)


def build_have(piece_index):
    """Announce that we have a specific piece."""
    payload = struct.pack('!I', piece_index)
    return build_message(MSG_HAVE, payload)


def build_bitfield(bitfield_bytes):
    """Send our complete bitfield to a peer."""
    return build_message(MSG_BITFIELD, bitfield_bytes)


def build_request(piece_index, begin=0, length=0):
    """
    Request a piece (or sub-piece) from a peer.
    For simplicity we request entire pieces, so begin=0 and
    length=full piece size.
    """
    payload = struct.pack('!III', piece_index, begin, length)
    return build_message(MSG_REQUEST, payload)


def build_piece(piece_index, begin, data):
    """
    Send piece data to a peer.
    """
    payload = struct.pack('!II', piece_index, begin) + data
    return build_message(MSG_PIECE, payload)


def build_cancel(piece_index, begin=0, length=0):
    """Cancel a previously sent request."""
    payload = struct.pack('!III', piece_index, begin, length)
    return build_message(MSG_CANCEL, payload)


async def read_handshake(reader):
    """
    Read and parse a handshake from an asyncio StreamReader.
    Returns (info_hash, peer_id).
    """
    # first byte is pstrlen
    pstrlen_raw = await reader.readexactly(1)
    pstrlen = pstrlen_raw[0]

    # read rest of handshake
    remaining = pstrlen + 8 + 32 + 32
    rest = await reader.readexactly(remaining)

    full = pstrlen_raw + rest
    return parse_handshake(full)


async def read_message(reader):
    """
    Read one message from an asyncio StreamReader.
    Returns (msg_id, payload) or (None, None) for keep-alive.
    """
    # read 4-byte length prefix
    length_raw = await reader.readexactly(4)
    length = struct.unpack('!I', length_raw)[0]

    if length == 0:
        # keep-alive
        return None, None

    # read the message body
    body = await reader.readexactly(length)
    msg_id = body[0]
    payload = body[1:]

    return msg_id, payload


def parse_have(payload):
    """Parse a HAVE message payload. Returns piece_index."""
    return struct.unpack('!I', payload)[0]


def parse_request(payload):
    """Parse a REQUEST message. Returns (piece_index, begin, length)."""
    return struct.unpack('!III', payload)


def parse_piece(payload):
    """Parse a PIECE message. Returns (piece_index, begin, data)."""
    piece_index, begin = struct.unpack('!II', payload[:8])
    data = payload[8:]
    return piece_index, begin, data


def parse_cancel(payload):
    """Parse a CANCEL message. Returns (piece_index, begin, length)."""
    return struct.unpack('!III', payload)
