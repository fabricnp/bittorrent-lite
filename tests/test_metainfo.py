"""
tests for src/metainfo - bencode, splitting, metainfo files, assembler

run with   python -m pytest tests/test_metainfo.py
or just    python tests/test_metainfo.py
"""

import os
import random
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.metainfo import bencode
from src.metainfo.bencode import BencodeError
from src.metainfo.file_splitter import (split_file, get_piece_data, verify_piece,
                                        compute_file_hash, piece_size, num_pieces_for)
from src.metainfo.metainfo import generate, parse, parse_bytes, MetainfoError
from src.metainfo.assembler import FileAssembler
from src.metainfo.__main__ import main as cli, parse_size

TRACKER = 'http://127.0.0.1:8000'


class TmpDir:
    """tiny helper so every test cleans up after itself"""
    def __enter__(self):
        self.path = tempfile.mkdtemp(prefix='bt_meta_')
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.path, ignore_errors=True)

    def file(self, name, size, seed=1):
        p = os.path.join(self.path, name)
        with open(p, 'wb') as f:
            f.write(random.Random(seed).randbytes(size))
        return p


def expect_error(exc, fn, *args):
    try:
        fn(*args)
    except exc:
        return
    raise AssertionError(f"{fn.__name__}{args!r} should have raised {exc.__name__}")


# ---------------- bencode ----------------

def test_bencode_basic_types():
    assert bencode.encode(42) == b'i42e'
    assert bencode.encode(0) == b'i0e'
    assert bencode.encode(-5) == b'i-5e'
    assert bencode.encode(b'hello') == b'5:hello'
    assert bencode.encode(b'') == b'0:'
    assert bencode.encode('test') == b'4:test'
    assert bencode.encode([b'hello', 42]) == b'l5:helloi42ee'
    assert bencode.encode([]) == b'le'
    assert bencode.encode({}) == b'de'


def test_bencode_dict_keys_sorted():
    assert bencode.encode({b'cow': b'moo', b'age': 3}) == b'd3:agei3e3:cow3:mooe'
    # str and bytes keys sort the same way
    assert bencode.encode({'cow': 1, b'age': 2}) == b'd3:agei2e3:cowi1ee'


def test_bencode_roundtrip():
    original = {
        b'name': b'test.txt',
        b'size': 12345,
        b'neg': -99,
        b'tags': [b'a', b'b', [b'nested', 0]],
        b'nested': {b'key': b'val', b'bin': bytes(range(256))},
    }
    assert bencode.decode(bencode.encode(original)) == original


def test_bencode_rejects_bad_input():
    bad = [
        b'', b'i42', b'ie', b'i-e', b'i03e', b'i-0e', b'iabce',
        b'5:abc',           # string runs past the end
        b'05:hello',        # leading zero in length
        b'l', b'li1e',      # unterminated list
        b'd3:keye',         # key with no value
        b'di1ei2ee',        # int as dict key
        b'd1:bi1e1:ai2ee',  # keys out of order
        b'd1:ai1e1:ai2ee',  # duplicate key
        b'i1ei2e',          # trailing data
        b'x',
    ]
    for data in bad:
        expect_error(BencodeError, bencode.decode, data)


def test_bencode_refuses_unsupported():
    expect_error(TypeError, bencode.encode, True)
    expect_error(TypeError, bencode.encode, 1.5)
    expect_error(TypeError, bencode.encode, None)
    expect_error(TypeError, bencode.encode, {1: b'x'})


# ---------------- splitting ----------------

def test_piece_size_math():
    assert num_pieces_for(1000, 256) == 4
    assert num_pieces_for(1024, 256) == 4
    assert num_pieces_for(1025, 256) == 5
    assert piece_size(0, 1000, 256) == 256
    assert piece_size(3, 1000, 256) == 1000 - 3 * 256
    assert piece_size(3, 1024, 256) == 256
    expect_error(IndexError, piece_size, 4, 1000, 256)
    expect_error(IndexError, piece_size, -1, 1000, 256)


def test_split_file():
    with TmpDir() as t:
        path = t.file('a.bin', 1000)
        res = split_file(path, piece_length=256)
        assert res['file_length'] == 1000
        assert res['num_pieces'] == 4
        assert len(res['piece_hashes']) == 4
        assert all(len(h) == 32 for h in res['piece_hashes'])
        assert res['file_hash'] == compute_file_hash(path)

        for i in range(4):
            piece = get_piece_data(path, i, 256)
            assert len(piece) == piece_size(i, 1000, 256)
            assert verify_piece(piece, res['piece_hashes'][i])
        assert not verify_piece(b'\x00' * 256, res['piece_hashes'][0])
        expect_error(IndexError, get_piece_data, path, 4, 256)


def test_split_exact_multiple_and_single_piece():
    with TmpDir() as t:
        res = split_file(t.file('b.bin', 1024), 256)
        assert res['num_pieces'] == 4

        res = split_file(t.file('c.bin', 10), 256)
        assert res['num_pieces'] == 1


def test_split_rejects_empty_and_bad_piece_len():
    with TmpDir() as t:
        empty = os.path.join(t.path, 'empty')
        open(empty, 'wb').close()
        expect_error(ValueError, split_file, empty, 256)
        p = t.file('x', 100)
        expect_error(ValueError, split_file, p, 0)
        expect_error(TypeError, split_file, p, 256.0)


# ---------------- metainfo ----------------

def test_generate_and_parse():
    with TmpDir() as t:
        src = t.file('movie.bin', 2400)
        out, ih = generate(src, TRACKER, piece_length=512,
                           output_path=os.path.join(t.path, 'movie.torrent'))
        assert len(ih) == 64

        m = parse(out)
        assert m['tracker_url'] == TRACKER
        assert m['file_name'] == 'movie.bin'
        assert m['file_length'] == 2400
        assert m['piece_length'] == 512
        assert m['num_pieces'] == 5
        assert m['info_hash_hex'] == ih
        assert m['file_hash'] == compute_file_hash(src)
        assert m['piece_hashes'] == split_file(src, 512)['piece_hashes']


def test_info_hash_ignores_tracker_and_date():
    # same file + piece size must give the same swarm id even with another tracker
    with TmpDir() as t:
        src = t.file('same.bin', 3000)
        _, a = generate(src, TRACKER, 1024, os.path.join(t.path, 'a.torrent'))
        _, b = generate(src, 'http://10.0.0.5:9000', 1024, os.path.join(t.path, 'b.torrent'))
        _, c = generate(src, TRACKER, 512, os.path.join(t.path, 'c.torrent'))
        assert a == b
        assert a != c


def test_generate_default_output_path():
    with TmpDir() as t:
        src = t.file('song.mp3', 500)
        out, _ = generate(src, TRACKER, 256)
        assert out == os.path.join(t.path, 'song.torrent')
        assert os.path.exists(out)


def test_generate_rejects_non_http_tracker():
    with TmpDir() as t:
        src = t.file('x.bin', 100)
        expect_error(MetainfoError, generate, src, 'udp://127.0.0.1:8000', 256)


def _torrent(info_overrides=None, drop=None):
    info = {
        b'file_name': b'f.bin', b'file_length': 1000, b'piece_length': 256,
        b'pieces': b'\x11' * 32 * 4,
    }
    info.update(info_overrides or {})
    if drop:
        del info[drop]
    return bencode.encode({b'tracker_url': TRACKER.encode(), b'info': info})


def test_parse_validation():
    parse_bytes(_torrent())  # baseline is fine
    expect_error(MetainfoError, parse_bytes, b'not bencode')
    expect_error(MetainfoError, parse_bytes, bencode.encode([1, 2]))
    expect_error(MetainfoError, parse_bytes, _torrent(drop=b'pieces'))
    expect_error(MetainfoError, parse_bytes, _torrent({b'pieces': b'\x11' * 33}))
    expect_error(MetainfoError, parse_bytes, _torrent({b'pieces': b'\x11' * 32 * 3}))
    expect_error(MetainfoError, parse_bytes, _torrent({b'file_length': 0}))
    expect_error(MetainfoError, parse_bytes, _torrent({b'piece_length': -1}))
    expect_error(MetainfoError, parse_bytes, _torrent({b'file_length': b'1000'}))
    expect_error(MetainfoError, parse_bytes, _torrent({b'file_hash': b'short'}))


def test_parse_rejects_path_traversal():
    for name in (b'../evil.bin', b'..', b'dir/f.bin', b''):
        expect_error(MetainfoError, parse_bytes, _torrent({b'file_name': name}))


# ---------------- assembler ----------------

def _setup(t, size=2000, plen=512, seed=7):
    src = t.file('orig.bin', size, seed)
    tor, _ = generate(src, TRACKER, plen, os.path.join(t.path, 'orig.torrent'))
    meta = parse(tor)
    return src, meta


def test_assembler_out_of_order():
    with TmpDir() as t:
        src, meta = _setup(t)
        dl = os.path.join(t.path, 'dl')
        asm = FileAssembler.from_metainfo(meta, dl)
        assert not asm.complete and asm.progress == 0.0
        assert asm.bytes_left == 2000

        order = list(range(meta['num_pieces']))
        random.Random(3).shuffle(order)
        for i in order:
            assert asm.add_piece(i, get_piece_data(src, i, 512))

        assert asm.complete and asm.progress == 1.0 and asm.bytes_left == 0
        # nothing kept in memory, it's all in the .part file
        assert os.path.exists(asm.part_path)

        out = asm.assemble()
        assert out == os.path.join(dl, 'orig.bin')
        assert not os.path.exists(asm.part_path)
        assert asm.verify_assembled_file(out)
        assert asm.verify_assembled_file(out, compute_file_hash(src))
        with open(out, 'rb') as a, open(src, 'rb') as b:
            assert a.read() == b.read()

        # can still serve pieces after assembling
        assert asm.read_piece(3) == get_piece_data(src, 3, 512)
        asm.close()


def test_assembler_rejects_bad_pieces():
    with TmpDir() as t:
        src, meta = _setup(t, size=1000, plen=256)
        asm = FileAssembler.from_metainfo(meta, os.path.join(t.path, 'dl'))

        assert not asm.add_piece(0, b'\x00' * 256)          # wrong content
        assert not asm.add_piece(0, get_piece_data(src, 0, 256)[:-1])  # truncated
        assert not asm.add_piece(99, b'x')                  # bad index
        assert not asm.add_piece(-1, b'x')
        assert asm.hash_failures == 2
        assert asm.read_piece(0) is None

        good = get_piece_data(src, 0, 256)
        assert asm.add_piece(0, good)
        assert asm.add_piece(0, good)  # duplicate is fine
        assert asm.have[0] and asm.downloaded_pieces == 1
        expect_error(RuntimeError, asm.assemble)
        asm.close()


def test_assembler_short_last_piece():
    with TmpDir() as t:
        src, meta = _setup(t, size=1000, plen=256)
        asm = FileAssembler.from_metainfo(meta, os.path.join(t.path, 'dl'))
        assert asm.piece_size(3) == 232
        assert asm.add_piece(3, get_piece_data(src, 3, 256))
        assert asm.read_piece(3) == get_piece_data(src, 3, 256)
        assert asm.read_block(3, 200, 32) == get_piece_data(src, 3, 256)[200:232]
        assert asm.read_block(3, 200, 33) is None
        assert asm.read_block(0, 0, 16) is None   # don't have piece 0
        asm.close()


def test_bitfield():
    with TmpDir() as t:
        _, meta = _setup(t, size=10 * 256, plen=256)  # 10 pieces -> 2 bytes
        asm = FileAssembler.from_metainfo(meta, os.path.join(t.path, 'dl'))
        assert asm.get_bitfield() == b'\x00\x00'
        asm.have[0] = asm.have[7] = asm.have[9] = True
        assert asm.get_bitfield() == bytes([0b10000001, 0b01000000])

        parsed = asm.parse_bitfield(bytes([0b10000001, 0b01000000]))
        assert parsed == asm.have
        assert asm.set_bitfield(b'\xff') == [True] * 8 + [False] * 2  # short one gets padded
        assert len(asm.parse_bitfield(b'\xff\xff\xff')) == 10


def test_pieces_data_view_compat():
    # client.py does `idx in assembler.pieces_data` and `assembler.pieces_data[idx]`
    with TmpDir() as t:
        src, meta = _setup(t)
        asm = FileAssembler.from_metainfo(meta, os.path.join(t.path, 'dl'))
        asm.add_piece(1, get_piece_data(src, 1, 512))
        view = asm.pieces_data
        assert 1 in view and 0 not in view and 'x' not in view
        assert view[1] == get_piece_data(src, 1, 512)
        assert len(view) == 1 and list(view) == [1]
        expect_error(KeyError, view.__getitem__, 0)
        asm.close()


def test_resume_from_part_file():
    with TmpDir() as t:
        src, meta = _setup(t)
        dl = os.path.join(t.path, 'dl')

        a = FileAssembler.from_metainfo(meta, dl)
        a.add_piece(0, get_piece_data(src, 0, 512))
        a.add_piece(2, get_piece_data(src, 2, 512))
        a.close()

        # "restart" the peer
        b = FileAssembler.from_metainfo(meta, dl)
        assert b.recheck() == 2
        assert b.have == [True, False, True, False]
        for i in b.missing_pieces():
            b.add_piece(i, get_piece_data(src, i, 512))
        b.assemble()

        # and again once the file is finished
        c = FileAssembler.from_metainfo(meta, dl)
        assert c.recheck() == meta['num_pieces'] and c.complete
        assert c.read_piece(0) == get_piece_data(src, 0, 512)
        b.close()
        c.close()


def test_seed_from_original():
    with TmpDir() as t:
        src, meta = _setup(t)
        dl = os.path.join(t.path, 'seed')
        asm = FileAssembler.from_metainfo(meta, dl)
        assert asm.seed_from(src) == meta['num_pieces']
        assert asm.complete
        assert asm.assemble() == src  # no copy made
        assert asm.read_block(2, 10, 20) == get_piece_data(src, 2, 512)[10:30]
        assert os.listdir(dl) == []
        asm.close()


def test_whole_file_hash_mismatch_is_caught():
    with TmpDir() as t:
        src, meta = _setup(t)
        meta = dict(meta, file_hash=b'\x00' * 32)  # pretend the torrent says something else
        asm = FileAssembler.from_metainfo(meta, os.path.join(t.path, 'dl'))
        for i in range(meta['num_pieces']):
            asm.add_piece(i, get_piece_data(src, i, 512))
        expect_error(RuntimeError, asm.assemble)
        asm.close()


# ---------------- cli ----------------

def test_cli():
    assert parse_size('256K') == 262144
    assert parse_size('100M') == 100 * 1024 * 1024
    assert parse_size('4096') == 4096
    with TmpDir() as t:
        f = os.path.join(t.path, 'data', 'test.bin')
        tor = os.path.join(t.path, 'test.torrent')
        assert cli(['mkfile', f, '300K', '--seed', '5']) == 0
        assert os.path.getsize(f) == 300 * 1024
        assert cli(['create', f, '-t', TRACKER, '-p', '64K', '-o', tor]) == 0
        assert cli(['info', tor]) == 0
        assert cli(['verify', tor, f]) == 0

        with open(f, 'r+b') as fh:  # flip one byte
            fh.seek(70000)
            b = fh.read(1)
            fh.seek(70000)
            fh.write(bytes([b[0] ^ 1]))
        assert cli(['verify', tor, f]) == 1


if __name__ == '__main__':
    tests = [(n, fn) for n, fn in sorted(globals().items()) if n.startswith('test_') and callable(fn)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok    {name}")
        except Exception as e:
            failed += 1
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
