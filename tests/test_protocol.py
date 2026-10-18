"""
test_protocol.py - Tests for the wire protocol.

Tests message building, parsing, and handshake.
"""

import os
import sys
import asyncio
import struct

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.peer.protocol import *


def test_handshake_build_parse():
    info_hash = b'\xab' * 32
    peer_id = 'test-peer-001'

    hs = build_handshake(info_hash, peer_id)
    assert len(hs) == HANDSHAKE_LEN

    parsed_hash, parsed_id = parse_handshake(hs)
    assert parsed_hash == info_hash
    assert parsed_id == peer_id.encode()

    print("  handshake build/parse: OK")


def test_handshake_wrong_protocol():
    # tamper with the protocol string
    hs = build_handshake(b'\x00' * 32, 'peer1')
    bad = bytearray(hs)
    bad[1] = 0xFF  # corrupt protocol string
    try:
        parse_handshake(bytes(bad))
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
    print("  handshake wrong protocol: OK")


def test_message_building():
    # choke
    msg = build_choke()
    length = struct.unpack('!I', msg[:4])[0]
    assert length == 1
    assert msg[4] == MSG_CHOKE

    # unchoke
    msg = build_unchoke()
    assert msg[4] == MSG_UNCHOKE

    # interested
    msg = build_interested()
    assert msg[4] == MSG_INTERESTED

    # have
    msg = build_have(42)
    assert msg[4] == MSG_HAVE
    piece_idx = struct.unpack('!I', msg[5:9])[0]
    assert piece_idx == 42

    print("  message building: OK")


def test_request_message():
    msg = build_request(10, 0, 16384)
    assert msg[4] == MSG_REQUEST

    payload = msg[5:]
    idx, begin, length = parse_request(payload)
    assert idx == 10
    assert begin == 0
    assert length == 16384

    print("  request message: OK")


def test_piece_message():
    data = b'hello world this is piece data'
    msg = build_piece(5, 0, data)
    assert msg[4] == MSG_PIECE

    payload = msg[5:]
    idx, begin, piece_data = parse_piece(payload)
    assert idx == 5
    assert begin == 0
    assert piece_data == data

    print("  piece message: OK")


def test_cancel_message():
    msg = build_cancel(7, 0, 16384)
    assert msg[4] == MSG_CANCEL

    payload = msg[5:]
    idx, begin, length = parse_cancel(payload)
    assert idx == 7
    assert begin == 0
    assert length == 16384

    print("  cancel message: OK")


def test_bitfield_message():
    bf = bytes([0b11110000, 0b10100000])
    msg = build_bitfield(bf)
    assert msg[4] == MSG_BITFIELD
    assert msg[5:] == bf

    print("  bitfield message: OK")


def test_keepalive():
    msg = build_keepalive()
    assert len(msg) == 4
    length = struct.unpack('!I', msg)[0]
    assert length == 0

    print("  keepalive: OK")


if __name__ == '__main__':
    print("Running protocol tests...")
    test_handshake_build_parse()
    test_handshake_wrong_protocol()
    test_message_building()
    test_request_message()
    test_piece_message()
    test_cancel_message()
    test_bitfield_message()
    test_keepalive()
    print("\nAll protocol tests passed!")
