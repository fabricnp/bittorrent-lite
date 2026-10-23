import pytest

from src.peer.protocol import (
    MessageType,
    decode_bitfield,
    decode_cancel,
    decode_have,
    decode_message,
    decode_piece,
    decode_request,
    encode_bitfield,
    encode_cancel,
    encode_have,
    encode_message,
    encode_piece,
    encode_request,
)


# HAVE, REQUEST and CANCEL share one payload shape: a 4-byte piece index.
INDEX_MESSAGES = [
    pytest.param(MessageType.HAVE, encode_have, decode_have, id="HAVE"),
    pytest.param(
        MessageType.REQUEST, encode_request, decode_request, id="REQUEST"
    ),
    pytest.param(MessageType.CANCEL, encode_cancel, decode_cancel, id="CANCEL"),
]


# ---------------------------------------------------------------------
# BITFIELD
# ---------------------------------------------------------------------

def test_bitfield_encoding_is_msb_first_with_zero_spare_bits():
    assert encode_bitfield([True, False, True]) == b"\xa0"
    assert encode_bitfield([True] * 8) == b"\xff"
    assert encode_bitfield([False] * 8 + [True]) == b"\x00\x80"


@pytest.mark.parametrize("piece_count", [0, 1, 7, 8, 9, 16, 17, 100])
def test_bitfield_round_trip(piece_count):
    original = [index % 3 == 0 for index in range(piece_count)]

    payload = encode_bitfield(original)

    assert len(payload) == (piece_count + 7) // 8
    assert decode_bitfield(payload, piece_count) == original


@pytest.mark.parametrize(
    "payload, piece_count, message",
    [
        (b"", 1, "Truncated"),
        (b"\xff", 9, "Truncated"),
        (b"\xff\x00", 8, "trailing"),
        (b"\x00", 0, "trailing"),
        (b"\xa1", 3, "spare"),
        (b"\x00\x01", 9, "spare"),
    ],
)
def test_bitfield_decode_rejects_malformed_payload(
    payload, piece_count, message
):
    with pytest.raises(ValueError, match=message):
        decode_bitfield(payload, piece_count)


def test_bitfield_rejects_invalid_arguments():
    with pytest.raises(TypeError):
        encode_bitfield([1, 0, 1])

    with pytest.raises(TypeError):
        decode_bitfield("abc", 8)

    with pytest.raises(ValueError):
        decode_bitfield(b"", -1)


# ---------------------------------------------------------------------
# HAVE / REQUEST / CANCEL
# ---------------------------------------------------------------------

@pytest.mark.parametrize("message_type, encode, decode", INDEX_MESSAGES)
@pytest.mark.parametrize("index", [0, 1, 255, 65536, 0xFFFFFFFF])
def test_index_message_round_trip(message_type, encode, decode, index):
    payload = encode(index)

    assert len(payload) == 4
    assert decode(payload) == index


@pytest.mark.parametrize("message_type, encode, decode", INDEX_MESSAGES)
def test_index_message_is_big_endian(message_type, encode, decode):
    assert encode(0x01020304) == bytes([1, 2, 3, 4])
    assert decode(bytes([1, 2, 3, 4])) == 0x01020304


@pytest.mark.parametrize("message_type, encode, decode", INDEX_MESSAGES)
@pytest.mark.parametrize("payload", [b"", b"\x00", b"\x00\x00\x00"])
def test_index_message_decode_rejects_truncated_payload(
    message_type, encode, decode, payload
):
    with pytest.raises(ValueError, match="Truncated"):
        decode(payload)


@pytest.mark.parametrize("message_type, encode, decode", INDEX_MESSAGES)
def test_index_message_decode_rejects_trailing_bytes(
    message_type, encode, decode
):
    with pytest.raises(ValueError, match="trailing"):
        decode(b"\x00\x00\x00\x01\x00")


@pytest.mark.parametrize("message_type, encode, decode", INDEX_MESSAGES)
def test_index_message_enforces_piece_count(message_type, encode, decode):
    assert decode(encode(4, 5), 5) == 4

    with pytest.raises(ValueError):
        encode(5, 5)

    with pytest.raises(ValueError):
        decode(encode(5), 5)


@pytest.mark.parametrize("message_type, encode, decode", INDEX_MESSAGES)
def test_index_message_rejects_invalid_arguments(
    message_type, encode, decode
):
    with pytest.raises(ValueError):
        encode(-1)

    with pytest.raises(ValueError):
        encode(0xFFFFFFFF + 1)

    with pytest.raises(TypeError):
        encode("1")

    with pytest.raises(TypeError):
        decode("abcd")


# ---------------------------------------------------------------------
# PIECE
# ---------------------------------------------------------------------

def test_piece_encoding_is_index_then_data():
    assert encode_piece(3, b"hello") == b"\x00\x00\x00\x03hello"


@pytest.mark.parametrize(
    "index, data",
    [
        (0, b"a"),
        (7, b"hello world"),
        (65536, bytes(range(256))),
        (0xFFFFFFFF, b"\x00\x00\x00\x00"),
    ],
)
def test_piece_round_trip(index, data):
    assert decode_piece(encode_piece(index, data)) == (index, data)


def test_piece_round_trip_large_piece():
    data = bytes(i % 251 for i in range(256 * 1024))

    assert decode_piece(encode_piece(42, data)) == (42, data)


@pytest.mark.parametrize(
    "payload",
    [b"", b"\x00", b"\x00\x00\x00", b"\x00\x00\x00\x05"],
)
def test_piece_decode_rejects_truncated_payload(payload):
    with pytest.raises(ValueError, match="Truncated"):
        decode_piece(payload)


def test_piece_enforces_piece_count():
    assert decode_piece(encode_piece(4, b"x", 5), 5) == (4, b"x")

    with pytest.raises(ValueError):
        encode_piece(5, b"x", 5)

    with pytest.raises(ValueError):
        decode_piece(encode_piece(5, b"x"), 5)


def test_piece_rejects_invalid_arguments():
    with pytest.raises(ValueError, match="empty"):
        encode_piece(0, b"")

    with pytest.raises(ValueError):
        encode_piece(-1, b"x")

    with pytest.raises(TypeError):
        encode_piece(0, "text")

    with pytest.raises(TypeError):
        encode_piece("0", b"x")

    with pytest.raises(TypeError):
        decode_piece("abcde")


# ---------------------------------------------------------------------
# Phase-3 payloads carried by the existing framing
# ---------------------------------------------------------------------

def test_phase3_payloads_survive_framing():
    bitfield = [True, False, True, True, False, False, True, False, True]

    message_type, payload = decode_message(
        encode_message(MessageType.BITFIELD, encode_bitfield(bitfield))
    )
    assert message_type == MessageType.BITFIELD
    assert decode_bitfield(payload, len(bitfield)) == bitfield

    message_type, payload = decode_message(
        encode_message(MessageType.PIECE, encode_piece(8, b"piece-data"))
    )
    assert message_type == MessageType.PIECE
    assert decode_piece(payload) == (8, b"piece-data")


@pytest.mark.parametrize("message_type, encode, decode", INDEX_MESSAGES)
def test_index_message_survives_framing(message_type, encode, decode):
    received_type, payload = decode_message(
        encode_message(message_type, encode(1234))
    )

    assert received_type == message_type
    assert decode(payload) == 1234