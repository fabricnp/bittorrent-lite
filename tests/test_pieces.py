import hashlib
import os

import pytest

from src.peer.pieces import (
    MissingPiecesError,
    assemble_pieces,
    hash_file_pieces,
    hash_piece,
    split_file_into_pieces,
    verify_piece,
    write_piece,
)


PIECE_SIZE = 10


def make_file(tmp_path, data: bytes, name: str = "source.bin") -> str:
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------
# 1. File splitting
# ---------------------------------------------------------------------

def test_split_file_yields_sequential_indexes_and_exact_data(tmp_path):
    data = bytes(range(25))
    path = make_file(tmp_path, data)

    pieces = list(split_file_into_pieces(path, PIECE_SIZE))

    assert [index for index, _ in pieces] == [0, 1, 2]
    assert pieces[0][1] == data[0:10]
    assert pieces[1][1] == data[10:20]
    assert pieces[2][1] == data[20:25]


def test_split_pieces_concatenate_back_to_original(tmp_path):
    data = os.urandom(1234)
    path = make_file(tmp_path, data)

    pieces = list(split_file_into_pieces(path, 100))

    assert b"".join(piece for _, piece in pieces) == data


def test_split_empty_file_yields_no_pieces(tmp_path):
    path = make_file(tmp_path, b"")

    assert list(split_file_into_pieces(path, PIECE_SIZE)) == []


@pytest.mark.parametrize("bad_size", [0, -1])
def test_split_rejects_non_positive_piece_size(tmp_path, bad_size):
    path = make_file(tmp_path, b"data")

    with pytest.raises(ValueError):
        list(split_file_into_pieces(path, bad_size))


@pytest.mark.parametrize("bad_size", [1.5, "10", None, True])
def test_split_rejects_non_int_piece_size(tmp_path, bad_size):
    path = make_file(tmp_path, b"data")

    with pytest.raises(TypeError):
        list(split_file_into_pieces(path, bad_size))


# ---------------------------------------------------------------------
# 2. Correct number of pieces
# ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "file_size, piece_size, expected_count",
    [
        (1, 10, 1),
        (9, 10, 1),
        (10, 10, 1),
        (11, 10, 2),
        (20, 10, 2),
        (25, 10, 3),
        (100, 10, 10),
        (101, 10, 11),
    ],
)
def test_piece_count_is_ceiling_of_size_over_piece_size(
    tmp_path, file_size, piece_size, expected_count
):
    path = make_file(tmp_path, b"x" * file_size)

    pieces = list(split_file_into_pieces(path, piece_size))

    assert len(pieces) == expected_count
    assert expected_count == (file_size + piece_size - 1) // piece_size


# ---------------------------------------------------------------------
# 3. SHA-256 piece hashes
# ---------------------------------------------------------------------

def test_hash_piece_matches_hashlib_sha256():
    data = b"hello world"

    assert hash_piece(data) == hashlib.sha256(data).hexdigest()


def test_hash_piece_known_vector():
    assert hash_piece(b"abc") == (
        "ba7816bf8f01cfea414140de5dae2223"
        "b00361a396177a9cb410ff61f20015ad"
    )


def test_hash_piece_is_64_lowercase_hex_characters():
    digest = hash_piece(b"anything")

    assert len(digest) == 64
    assert digest == digest.lower()
    assert set(digest) <= set("0123456789abcdef")


def test_hash_piece_accepts_bytearray():
    assert hash_piece(bytearray(b"abc")) == hash_piece(b"abc")


@pytest.mark.parametrize("bad_data", ["text", 123, None])
def test_hash_piece_rejects_non_bytes(bad_data):
    with pytest.raises(TypeError):
        hash_piece(bad_data)


def test_different_data_gives_different_hash():
    assert hash_piece(b"piece A") != hash_piece(b"piece B")


def test_hash_file_pieces_matches_independent_computation(tmp_path):
    data = os.urandom(95)
    path = make_file(tmp_path, data)

    expected = [
        sha256_hex(data[offset:offset + PIECE_SIZE])
        for offset in range(0, len(data), PIECE_SIZE)
    ]

    assert hash_file_pieces(path, PIECE_SIZE) == expected


def test_hash_file_pieces_returns_one_hash_per_piece_in_order(tmp_path):
    data = b"A" * 10 + b"B" * 10 + b"C" * 5
    path = make_file(tmp_path, data)

    assert hash_file_pieces(path, PIECE_SIZE) == [
        sha256_hex(b"A" * 10),
        sha256_hex(b"B" * 10),
        sha256_hex(b"C" * 5),
    ]


# ---------------------------------------------------------------------
# 4. Final smaller piece
# ---------------------------------------------------------------------

def test_final_piece_is_smaller_when_size_not_a_multiple(tmp_path):
    path = make_file(tmp_path, b"x" * 25)

    pieces = list(split_file_into_pieces(path, PIECE_SIZE))

    assert [len(piece) for _, piece in pieces] == [10, 10, 5]


def test_final_piece_is_full_size_when_size_is_exact_multiple(tmp_path):
    path = make_file(tmp_path, b"x" * 30)

    pieces = list(split_file_into_pieces(path, PIECE_SIZE))

    assert [len(piece) for _, piece in pieces] == [10, 10, 10]


def test_file_smaller_than_piece_size_gives_one_short_piece(tmp_path):
    path = make_file(tmp_path, b"tiny")

    pieces = list(split_file_into_pieces(path, PIECE_SIZE))

    assert pieces == [(0, b"tiny")]


def test_final_smaller_piece_hash_covers_only_its_own_bytes(tmp_path):
    data = b"x" * 10 + b"yz"
    path = make_file(tmp_path, data)

    hashes = hash_file_pieces(path, PIECE_SIZE)

    assert hashes[-1] == sha256_hex(b"yz")
    assert hashes[-1] != sha256_hex(b"yz" + b"\x00" * 8)


# ---------------------------------------------------------------------
# 5. Valid piece verification
# ---------------------------------------------------------------------

def test_verify_piece_accepts_matching_hash():
    data = b"valid piece data"

    assert verify_piece(data, sha256_hex(data)) is True


def test_verify_piece_accepts_uppercase_expected_hash():
    data = b"valid piece data"

    assert verify_piece(data, sha256_hex(data).upper()) is True


def test_verify_piece_accepts_every_piece_of_a_real_file(tmp_path):
    data = os.urandom(77)
    path = make_file(tmp_path, data)

    hashes = hash_file_pieces(path, PIECE_SIZE)

    for index, piece in split_file_into_pieces(path, PIECE_SIZE):
        assert verify_piece(piece, hashes[index]) is True


def test_verify_piece_accepts_bytearray_data():
    data = b"valid piece data"

    assert verify_piece(bytearray(data), sha256_hex(data)) is True


# ---------------------------------------------------------------------
# 6. Invalid piece verification
# ---------------------------------------------------------------------

def test_verify_piece_rejects_corrupted_data():
    data = b"original piece data"
    expected = sha256_hex(data)

    assert verify_piece(b"tampered piece data", expected) is False


def test_verify_piece_rejects_single_flipped_bit():
    data = bytearray(b"original piece data")
    expected = sha256_hex(bytes(data))

    data[0] ^= 0x01

    assert verify_piece(bytes(data), expected) is False


def test_verify_piece_rejects_truncated_data():
    data = b"original piece data"

    assert verify_piece(data[:-1], sha256_hex(data)) is False


def test_verify_piece_rejects_data_with_extra_byte():
    data = b"original piece data"

    assert verify_piece(data + b"\x00", sha256_hex(data)) is False


def test_verify_piece_rejects_hash_of_a_different_piece():
    assert verify_piece(b"piece 1", sha256_hex(b"piece 2")) is False


@pytest.mark.parametrize(
    "bad_hash",
    [
        "",
        "abc",
        "g" * 64,
        "a" * 63,
        "a" * 65,
        b"a" * 64,
        None,
        123,
    ],
)
def test_verify_piece_rejects_malformed_expected_hash(bad_hash):
    with pytest.raises(ValueError):
        verify_piece(b"data", bad_hash)


# ---------------------------------------------------------------------
# 7. Piece writing
# ---------------------------------------------------------------------

def test_write_piece_creates_file_with_exact_contents(tmp_path):
    pieces_dir = str(tmp_path / "pieces")
    data = b"piece zero data"

    path = write_piece(pieces_dir, 0, data)

    assert os.path.isfile(path)

    with open(path, "rb") as file:
        assert file.read() == data


def test_write_piece_creates_missing_directory(tmp_path):
    pieces_dir = tmp_path / "does" / "not" / "exist"

    assert not pieces_dir.exists()

    write_piece(str(pieces_dir), 0, b"data")

    assert pieces_dir.is_dir()


def test_write_piece_returns_path_inside_pieces_dir(tmp_path):
    pieces_dir = str(tmp_path / "pieces")

    path = write_piece(pieces_dir, 3, b"data")

    assert os.path.dirname(path) == pieces_dir


def test_write_piece_uses_distinct_file_per_index(tmp_path):
    pieces_dir = str(tmp_path / "pieces")

    path_a = write_piece(pieces_dir, 0, b"A")
    path_b = write_piece(pieces_dir, 1, b"B")

    assert path_a != path_b

    with open(path_a, "rb") as file:
        assert file.read() == b"A"

    with open(path_b, "rb") as file:
        assert file.read() == b"B"


def test_write_piece_overwrites_existing_piece(tmp_path):
    pieces_dir = str(tmp_path / "pieces")

    write_piece(pieces_dir, 0, b"old contents that are longer")
    path = write_piece(pieces_dir, 0, b"new")

    with open(path, "rb") as file:
        assert file.read() == b"new"


def test_write_piece_leaves_no_temporary_file_behind(tmp_path):
    pieces_dir = tmp_path / "pieces"

    write_piece(str(pieces_dir), 0, b"data")

    leftovers = [
        name for name in os.listdir(pieces_dir) if name.endswith(".tmp")
    ]

    assert leftovers == []


def test_write_piece_accepts_bytearray(tmp_path):
    path = write_piece(str(tmp_path / "pieces"), 0, bytearray(b"abc"))

    with open(path, "rb") as file:
        assert file.read() == b"abc"


@pytest.mark.parametrize("bad_index", [-1, -100])
def test_write_piece_rejects_negative_index(tmp_path, bad_index):
    with pytest.raises(ValueError):
        write_piece(str(tmp_path / "pieces"), bad_index, b"data")


@pytest.mark.parametrize("bad_index", [1.0, "0", None, True])
def test_write_piece_rejects_non_int_index(tmp_path, bad_index):
    with pytest.raises(TypeError):
        write_piece(str(tmp_path / "pieces"), bad_index, b"data")


def test_write_piece_rejects_index_at_or_beyond_piece_count(tmp_path):
    pieces_dir = str(tmp_path / "pieces")

    with pytest.raises(ValueError):
        write_piece(pieces_dir, 3, b"data", piece_count=3)

    with pytest.raises(ValueError):
        write_piece(pieces_dir, 10, b"data", piece_count=3)


def test_write_piece_accepts_last_valid_index(tmp_path):
    path = write_piece(str(tmp_path / "pieces"), 2, b"data", piece_count=3)

    assert os.path.isfile(path)


@pytest.mark.parametrize("bad_data", ["text", 123, None])
def test_write_piece_rejects_non_bytes_data(tmp_path, bad_data):
    with pytest.raises(TypeError):
        write_piece(str(tmp_path / "pieces"), 0, bad_data)


def test_rejected_write_does_not_create_a_piece_file(tmp_path):
    pieces_dir = tmp_path / "pieces"

    with pytest.raises(ValueError):
        write_piece(str(pieces_dir), -1, b"data")

    assert not pieces_dir.exists() or os.listdir(pieces_dir) == []


# ---------------------------------------------------------------------
# 8. Correct piece assembly
# ---------------------------------------------------------------------

def test_assemble_pieces_reproduces_original_file(tmp_path):
    data = os.urandom(257)
    source = make_file(tmp_path, data)
    pieces_dir = str(tmp_path / "pieces")
    output = str(tmp_path / "rebuilt.bin")

    count = 0

    for index, piece in split_file_into_pieces(source, PIECE_SIZE):
        write_piece(pieces_dir, index, piece)
        count += 1

    assemble_pieces(pieces_dir, output, count)

    with open(output, "rb") as file:
        assert file.read() == data


def test_assembled_file_has_same_sha256_as_original(tmp_path):
    data = os.urandom(1000)
    source = make_file(tmp_path, data)
    pieces_dir = str(tmp_path / "pieces")
    output = str(tmp_path / "rebuilt.bin")

    count = 0

    for index, piece in split_file_into_pieces(source, 64):
        write_piece(pieces_dir, index, piece)
        count += 1

    assemble_pieces(pieces_dir, output, count)

    with open(output, "rb") as file:
        assert sha256_hex(file.read()) == sha256_hex(data)


def test_assemble_pieces_orders_by_index_not_write_order(tmp_path):
    pieces_dir = str(tmp_path / "pieces")
    output = str(tmp_path / "rebuilt.bin")

    write_piece(pieces_dir, 2, b"CC")
    write_piece(pieces_dir, 0, b"AA")
    write_piece(pieces_dir, 1, b"BB")

    assemble_pieces(pieces_dir, output, 3)

    with open(output, "rb") as file:
        assert file.read() == b"AABBCC"


def test_assemble_pieces_orders_correctly_past_ten_pieces(tmp_path):
    pieces_dir = str(tmp_path / "pieces")
    output = str(tmp_path / "rebuilt.bin")

    for index in range(12):
        write_piece(pieces_dir, index, bytes([index]))

    assemble_pieces(pieces_dir, output, 12)

    with open(output, "rb") as file:
        assert file.read() == bytes(range(12))


def test_assemble_pieces_handles_smaller_final_piece(tmp_path):
    data = b"x" * 25
    source = make_file(tmp_path, data)
    pieces_dir = str(tmp_path / "pieces")
    output = str(tmp_path / "rebuilt.bin")

    for index, piece in split_file_into_pieces(source, PIECE_SIZE):
        write_piece(pieces_dir, index, piece)

    assemble_pieces(pieces_dir, output, 3)

    assert os.path.getsize(output) == 25


def test_assemble_pieces_leaves_no_temporary_file_behind(tmp_path):
    pieces_dir = str(tmp_path / "pieces")
    output = tmp_path / "rebuilt.bin"

    write_piece(pieces_dir, 0, b"data")

    assemble_pieces(pieces_dir, str(output), 1)

    assert output.is_file()
    assert not (tmp_path / "rebuilt.bin.tmp").exists()


def test_assemble_pieces_with_zero_pieces_creates_empty_file(tmp_path):
    pieces_dir = tmp_path / "pieces"
    pieces_dir.mkdir()
    output = tmp_path / "rebuilt.bin"

    assemble_pieces(str(pieces_dir), str(output), 0)

    assert output.read_bytes() == b""


def test_assemble_pieces_rejects_negative_piece_count(tmp_path):
    with pytest.raises(ValueError):
        assemble_pieces(
            str(tmp_path / "pieces"),
            str(tmp_path / "out.bin"),
            -1,
        )


@pytest.mark.parametrize("bad_count", [1.5, "3", None, True])
def test_assemble_pieces_rejects_non_int_piece_count(tmp_path, bad_count):
    with pytest.raises(TypeError):
        assemble_pieces(
            str(tmp_path / "pieces"),
            str(tmp_path / "out.bin"),
            bad_count,
        )


def test_full_roundtrip_with_verification(tmp_path):
    """Split, hash, store only verified pieces, then assemble."""
    data = os.urandom(333)
    source = make_file(tmp_path, data)
    pieces_dir = str(tmp_path / "pieces")
    output = str(tmp_path / "rebuilt.bin")

    hashes = hash_file_pieces(source, PIECE_SIZE)

    for index, piece in split_file_into_pieces(source, PIECE_SIZE):
        assert verify_piece(piece, hashes[index])
        write_piece(pieces_dir, index, piece, piece_count=len(hashes))

    assemble_pieces(pieces_dir, output, len(hashes))

    with open(output, "rb") as file:
        assert file.read() == data


# ---------------------------------------------------------------------
# 9. Missing-piece detection
# ---------------------------------------------------------------------

def test_assemble_raises_when_a_middle_piece_is_missing(tmp_path):
    pieces_dir = str(tmp_path / "pieces")
    output = str(tmp_path / "rebuilt.bin")

    write_piece(pieces_dir, 0, b"AA")
    write_piece(pieces_dir, 2, b"CC")

    with pytest.raises(MissingPiecesError) as error:
        assemble_pieces(pieces_dir, output, 3)

    assert error.value.missing == [1]


def test_assemble_reports_all_missing_pieces_in_order(tmp_path):
    pieces_dir = str(tmp_path / "pieces")
    output = str(tmp_path / "rebuilt.bin")

    write_piece(pieces_dir, 1, b"BB")
    write_piece(pieces_dir, 3, b"DD")

    with pytest.raises(MissingPiecesError) as error:
        assemble_pieces(pieces_dir, output, 5)

    assert error.value.missing == [0, 2, 4]


def test_assemble_detects_missing_first_and_last_pieces(tmp_path):
    pieces_dir = str(tmp_path / "pieces")
    output = str(tmp_path / "rebuilt.bin")

    write_piece(pieces_dir, 1, b"BB")

    with pytest.raises(MissingPiecesError) as error:
        assemble_pieces(pieces_dir, output, 3)

    assert error.value.missing == [0, 2]


def test_assemble_reports_every_piece_when_none_exist(tmp_path):
    pieces_dir = tmp_path / "pieces"
    pieces_dir.mkdir()

    with pytest.raises(MissingPiecesError) as error:
        assemble_pieces(str(pieces_dir), str(tmp_path / "out.bin"), 4)

    assert error.value.missing == [0, 1, 2, 3]


def test_assemble_reports_every_piece_when_directory_is_absent(tmp_path):
    with pytest.raises(MissingPiecesError) as error:
        assemble_pieces(
            str(tmp_path / "no_such_dir"),
            str(tmp_path / "out.bin"),
            2,
        )

    assert error.value.missing == [0, 1]


def test_assemble_writes_nothing_when_pieces_are_missing(tmp_path):
    pieces_dir = str(tmp_path / "pieces")
    output = tmp_path / "rebuilt.bin"

    write_piece(pieces_dir, 0, b"AA")

    with pytest.raises(MissingPiecesError):
        assemble_pieces(pieces_dir, str(output), 2)

    assert not output.exists()
    assert not (tmp_path / "rebuilt.bin.tmp").exists()


def test_assemble_does_not_overwrite_existing_output_when_incomplete(tmp_path):
    pieces_dir = str(tmp_path / "pieces")
    output = tmp_path / "rebuilt.bin"
    output.write_bytes(b"previous good output")

    write_piece(pieces_dir, 0, b"AA")

    with pytest.raises(MissingPiecesError):
        assemble_pieces(pieces_dir, str(output), 2)

    assert output.read_bytes() == b"previous good output"


def test_extra_pieces_beyond_piece_count_are_not_treated_as_missing(tmp_path):
    pieces_dir = str(tmp_path / "pieces")
    output = str(tmp_path / "rebuilt.bin")

    write_piece(pieces_dir, 0, b"AA")
    write_piece(pieces_dir, 1, b"BB")
    write_piece(pieces_dir, 2, b"CC")

    assemble_pieces(pieces_dir, output, 2)

    with open(output, "rb") as file:
        assert file.read() == b"AABB"


def test_missing_pieces_error_is_an_exception_with_readable_message():
    error = MissingPiecesError([1, 4])

    assert isinstance(error, Exception)
    assert error.missing == [1, 4]
    assert "1" in str(error)
    assert "4" in str(error)


def test_assemble_succeeds_once_the_missing_piece_arrives(tmp_path):
    pieces_dir = str(tmp_path / "pieces")
    output = str(tmp_path / "rebuilt.bin")

    write_piece(pieces_dir, 0, b"AA")
    write_piece(pieces_dir, 2, b"CC")

    with pytest.raises(MissingPiecesError) as error:
        assemble_pieces(pieces_dir, output, 3)

    assert error.value.missing == [1]

    write_piece(pieces_dir, 1, b"BB")

    assemble_pieces(pieces_dir, output, 3)

    with open(output, "rb") as file:
        assert file.read() == b"AABBCC"