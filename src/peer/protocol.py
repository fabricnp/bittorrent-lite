import struct
from enum import IntEnum, Enum


class MessageType(IntEnum):
    HANDSHAKE = 1
    BITFIELD = 2
    HAVE = 3
    REQUEST = 4
    PIECE = 5
    CANCEL = 6


class PeerState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    HANDSHAKING = "handshaking"
    CONNECTED = "connected"
    CLOSED = "closed"


PROTOCOL_ID = "P2P-FILE-DIST/1"

LENGTH_SIZE = 4
TYPE_SIZE = 1
FIELD_LENGTH_SIZE = 2
MAX_FIELD_SIZE = 0xFFFF
MAX_FRAME_LENGTH = 0xFFFFFFFF


class Handshake:
    def __init__(
        self,
        protocol_id: str,
        swarm_id: str,
        peer_id: str,
    ):
        self.protocol_id = protocol_id
        self.swarm_id = swarm_id
        self.peer_id = peer_id

    def __repr__(self):
        return (
            f"Handshake("
            f"protocol_id={self.protocol_id!r}, "
            f"swarm_id={self.swarm_id!r}, "
            f"peer_id={self.peer_id!r})"
        )


def encode_handshake(handshake: Handshake) -> bytes:
    """
    Convert a Handshake object into the byte representation
    defined in docs/peer_protocol.md.
    """
    if not isinstance(handshake, Handshake):
        raise TypeError("handshake must be a Handshake")

    payload = bytearray()

    for name in ("protocol_id", "swarm_id", "peer_id"):
        value = getattr(handshake, name)

        if not isinstance(value, str):
            raise TypeError(f"{name} must be a str")

        data = value.encode("utf-8")

        if len(data) > MAX_FIELD_SIZE:
            raise ValueError(f"{name} is too long for a 2-byte length")

        payload += struct.pack("!H", len(data)) + data

    return bytes(payload)


def decode_handshake(data: bytes) -> Handshake:
    """
    Convert handshake bytes back into a Handshake object.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("data must be bytes")

    offset = 0
    fields = []

    for name in ("protocol_id", "swarm_id", "peer_id"):
        if len(data) - offset < FIELD_LENGTH_SIZE:
            raise ValueError(f"Truncated handshake: missing {name} length")

        (length,) = struct.unpack_from("!H", data, offset)
        offset += FIELD_LENGTH_SIZE

        if len(data) - offset < length:
            raise ValueError(f"Truncated handshake: incomplete {name}")

        try:
            fields.append(data[offset:offset + length].decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ValueError(f"Invalid UTF-8 in {name}") from exc

        offset += length

    if offset != len(data):
        raise ValueError("Unexpected trailing bytes in handshake")

    return Handshake(*fields)


def validate_handshake(
    handshake: Handshake,
    expected_protocol: str,
    expected_swarm: str,
) -> bool:

    if handshake.protocol_id != expected_protocol:
        return False

    if handshake.swarm_id != expected_swarm:
        return False

    if not handshake.peer_id:
        return False

    return True


def encode_message(
    message_type: MessageType,
    payload: bytes,
) -> bytes:
    """
    Create:

        LENGTH | MESSAGE TYPE | PAYLOAD
    """
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("payload must be bytes")

    message_type = MessageType(message_type)

    length = TYPE_SIZE + len(payload)

    if length > MAX_FRAME_LENGTH:
        raise ValueError("payload too large for a 4-byte length")

    return struct.pack("!IB", length, message_type) + bytes(payload)


def decode_message(data: bytes):
    """
    Decode one complete framed message.

    Returns:
        (message_type, payload)
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("data must be bytes")

    if len(data) < LENGTH_SIZE + TYPE_SIZE:
        raise ValueError("Incomplete frame: header too short")

    (length,) = struct.unpack_from("!I", data, 0)

    if length < TYPE_SIZE:
        raise ValueError("Malformed frame: length must be at least 1")

    frame_size = LENGTH_SIZE + length

    if len(data) < frame_size:
        raise ValueError("Incomplete frame: missing bytes")

    if len(data) > frame_size:
        raise ValueError("Unexpected trailing bytes after frame")

    try:
        message_type = MessageType(data[LENGTH_SIZE])
    except ValueError as exc:
        raise ValueError(
            f"Invalid message type: {data[LENGTH_SIZE]}"
        ) from exc

    return message_type, bytes(data[LENGTH_SIZE + TYPE_SIZE:])



# ---------------------------------------------------------------------
# PHASE 3 PAYLOADS
#
# These helpers build and parse only the PAYLOAD part of a message.
# The framing (LENGTH | MESSAGE TYPE | PAYLOAD) is still done by
# encode_message() / decode_message().
#
# All integers are unsigned and big-endian (network byte order).
#
#   BITFIELD : ceil(piece_count / 8) bytes
#                bit 7 (most significant) of byte 0 = piece 0
#                bit 6 of byte 0 = piece 1, ... bit 0 of byte 0 = piece 7
#                bit 7 of byte 1 = piece 8, and so on
#                1 = peer has the piece, 0 = peer does not
#                unused (spare) bits in the last byte MUST be 0
#   HAVE     : 4-byte piece index
#   REQUEST  : 4-byte piece index
#   PIECE    : 4-byte piece index + piece data (at least 1 byte)
#   CANCEL   : 4-byte piece index
#
# Piece indexes start at 0. BITFIELD has no length field of its own, so
# the receiver must know piece_count (from the metainfo) to decode it.
# ---------------------------------------------------------------------

PIECE_INDEX_SIZE = 4
MAX_PIECE_INDEX = 0xFFFFFFFF


def _check_piece_index(index: int, piece_count: int = None) -> None:
    """Validate a piece index (and its upper bound if piece_count is given)."""
    if not isinstance(index, int) or isinstance(index, bool):
        raise TypeError("piece index must be an int")

    if index < 0 or index > MAX_PIECE_INDEX:
        raise ValueError(f"Invalid piece index: {index}")

    if piece_count is not None and index >= piece_count:
        raise ValueError(
            f"Invalid piece index: {index} (piece_count is {piece_count})"
        )


def _encode_index_payload(index: int, piece_count: int = None) -> bytes:
    _check_piece_index(index, piece_count)

    return struct.pack("!I", index)


def _decode_index_payload(
    payload: bytes,
    message_name: str,
    piece_count: int = None,
) -> int:
    """Decode a payload that must be exactly one 4-byte piece index."""
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("payload must be bytes")

    if len(payload) < PIECE_INDEX_SIZE:
        raise ValueError(f"Truncated {message_name} payload")

    if len(payload) > PIECE_INDEX_SIZE:
        raise ValueError(f"Unexpected trailing bytes in {message_name} payload")

    (index,) = struct.unpack("!I", payload)

    _check_piece_index(index, piece_count)

    return index


# ---------------------------- BITFIELD -------------------------------

def encode_bitfield(has_piece: list) -> bytes:
    """
    Encode piece availability, one bit per piece.

    has_piece[i] is True if the peer has piece i.
    Spare bits in the last byte are set to 0.
    """
    if not isinstance(has_piece, (list, tuple)):
        raise TypeError("has_piece must be a list or tuple of bool")

    payload = bytearray((len(has_piece) + 7) // 8)

    for index, flag in enumerate(has_piece):
        if not isinstance(flag, bool):
            raise TypeError("has_piece must contain only bool values")

        if flag:
            payload[index // 8] |= 0x80 >> (index % 8)

    return bytes(payload)


def decode_bitfield(payload: bytes, piece_count: int) -> list:
    """
    Decode a BITFIELD payload into a list of piece_count bools.

    Rejects a payload of the wrong length and non-zero spare bits.
    """
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("payload must be bytes")

    if not isinstance(piece_count, int) or isinstance(piece_count, bool):
        raise TypeError("piece_count must be an int")

    if piece_count < 0:
        raise ValueError("piece_count cannot be negative")

    expected_size = (piece_count + 7) // 8

    if len(payload) < expected_size:
        raise ValueError("Truncated BITFIELD payload")

    if len(payload) > expected_size:
        raise ValueError("Unexpected trailing bytes in BITFIELD payload")

    spare_bits = piece_count % 8

    if spare_bits and payload[-1] & (0xFF >> spare_bits):
        raise ValueError("BITFIELD spare bits must be zero")

    return [
        bool(payload[index // 8] & (0x80 >> (index % 8)))
        for index in range(piece_count)
    ]


# ------------------------------ HAVE ---------------------------------

def encode_have(index: int, piece_count: int = None) -> bytes:
    """HAVE payload: 4-byte piece index."""
    return _encode_index_payload(index, piece_count)


def decode_have(payload: bytes, piece_count: int = None) -> int:
    """Return the piece index from a HAVE payload."""
    return _decode_index_payload(payload, "HAVE", piece_count)


# ----------------------------- REQUEST -------------------------------

def encode_request(index: int, piece_count: int = None) -> bytes:
    """REQUEST payload: 4-byte index of the requested piece."""
    return _encode_index_payload(index, piece_count)


def decode_request(payload: bytes, piece_count: int = None) -> int:
    """Return the requested piece index from a REQUEST payload."""
    return _decode_index_payload(payload, "REQUEST", piece_count)


# ------------------------------ PIECE --------------------------------

def encode_piece(index: int, data: bytes, piece_count: int = None) -> bytes:
    """PIECE payload: 4-byte piece index followed by the piece data."""
    _check_piece_index(index, piece_count)

    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("data must be bytes")

    if len(data) == 0:
        raise ValueError("PIECE data cannot be empty")

    return struct.pack("!I", index) + bytes(data)


def decode_piece(payload: bytes, piece_count: int = None):
    """
    Decode a PIECE payload.

    Returns:
        (index, data)
    """
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("payload must be bytes")

    if len(payload) < PIECE_INDEX_SIZE + 1:
        raise ValueError("Truncated PIECE payload")

    (index,) = struct.unpack_from("!I", payload, 0)

    _check_piece_index(index, piece_count)

    return index, bytes(payload[PIECE_INDEX_SIZE:])


# ----------------------------- CANCEL --------------------------------

def encode_cancel(index: int, piece_count: int = None) -> bytes:
    """CANCEL payload: 4-byte index of the piece being cancelled."""
    return _encode_index_payload(index, piece_count)


def decode_cancel(payload: bytes, piece_count: int = None) -> int:
    """Return the cancelled piece index from a CANCEL payload."""
    return _decode_index_payload(payload, "CANCEL", piece_count)