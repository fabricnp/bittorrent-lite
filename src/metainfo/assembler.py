"""
assembler.py - store verified pieces on disk and rebuild the original file

Pieces arrive in random order from different peers. Instead of keeping
them in memory (100 MB file x 8 peers in one experiment process gets
ugly fast) every verified piece is written straight to its offset in
<file_name>.part inside download_dir. The .part file is pre-sized to the
full file length, so once every piece is in, "assembling" is just a
final hash check and a rename.

Seeders don't copy anything, seed_from() points the assembler at the
original file and verifies it in place.
"""

import logging
import os

from .file_splitter import verify_piece, compute_file_hash, num_pieces_for, piece_size

log = logging.getLogger('assembler')


class FileAssembler:

    def __init__(self, file_name, file_length, piece_length, piece_hashes,
                 download_dir='.', file_hash=None):
        if num_pieces_for(file_length, piece_length) != len(piece_hashes):
            raise ValueError("piece_hashes doesn't match file_length / piece_length")

        self.file_name = file_name
        self.file_length = file_length
        self.piece_length = piece_length
        self.piece_hashes = list(piece_hashes)
        self.num_pieces = len(self.piece_hashes)
        self.download_dir = download_dir
        self.file_hash = file_hash  # whole file sha256, None if the torrent didn't have it

        self.have = [False] * self.num_pieces
        self.hash_failures = 0  # how many bad pieces we threw away, handy for the report

        os.makedirs(download_dir, exist_ok=True)
        self.final_path = os.path.join(download_dir, file_name)
        self.part_path = self.final_path + '.part'

        self._fh = None          # open handle to whatever file holds our data
        self._data_path = None   # path of that file
        self._writable = False
        self.assembled = False

    @classmethod
    def from_metainfo(cls, info, download_dir='.'):
        """build straight from metainfo.parse() output"""
        return cls(
            file_name=info['file_name'],
            file_length=info['file_length'],
            piece_length=info['piece_length'],
            piece_hashes=info['piece_hashes'],
            download_dir=download_dir,
            file_hash=info.get('file_hash'),
        )

    # ---------- state ----------

    @property
    def complete(self):
        return all(self.have)

    @property
    def downloaded_pieces(self):
        return sum(self.have)

    @property
    def progress(self):
        """0.0 .. 1.0"""
        if self.num_pieces == 0:
            return 1.0
        return self.downloaded_pieces / self.num_pieces

    @property
    def bytes_left(self):
        return sum(self.piece_size(i) for i in self.missing_pieces())

    def piece_size(self, index):
        return piece_size(index, self.file_length, self.piece_length)

    def missing_pieces(self):
        return [i for i in range(self.num_pieces) if not self.have[i]]

    # ---------- bitfields ----------

    def get_bitfield(self):
        """Our pieces as a bitfield, piece 0 = high bit of byte 0, spare bits zero."""
        bf = bytearray((self.num_pieces + 7) // 8)
        for i, has in enumerate(self.have):
            if has:
                bf[i >> 3] |= 0x80 >> (i & 7)
        return bytes(bf)

    def parse_bitfield(self, bitfield_bytes):
        """
        Turn a peer's bitfield into a list of bools, one per piece.
        Short bitfields are padded with False, extra bytes/bits ignored.
        """
        out = []
        n = len(bitfield_bytes)
        for i in range(self.num_pieces):
            byte_idx = i >> 3
            out.append(byte_idx < n and bool(bitfield_bytes[byte_idx] & (0x80 >> (i & 7))))
        return out

    # old name, peer_connection.py still calls this
    set_bitfield = parse_bitfield

    # ---------- disk ----------

    def _open_part(self):
        if self._fh is not None and self._writable:
            return
        self.close()
        mode = 'r+b' if os.path.exists(self.part_path) else 'w+b'
        self._fh = open(self.part_path, mode)
        # pre-size so we can write any piece at its offset. sparse on most filesystems
        self._fh.truncate(self.file_length)
        self._data_path = self.part_path
        self._writable = True

    def _open_readonly(self, path):
        self.close()
        self._fh = open(path, 'rb')
        self._data_path = path
        self._writable = False

    def close(self):
        if self._fh is not None:
            try:
                self._fh.close()
            finally:
                self._fh = None
                self._data_path = None
                self._writable = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------- pieces ----------

    def add_piece(self, index, data):
        """
        Verify a downloaded piece and write it to disk.
        Returns True if accepted (or we already had it), False if rejected.
        """
        if index < 0 or index >= self.num_pieces:
            log.warning("piece index %d out of range", index)
            return False

        if self.have[index]:
            return True

        if self.assembled:
            # shouldn't happen, if we're assembled we have everything
            return True

        if len(data) != self.piece_size(index):
            log.warning("piece %d has wrong size %d (expected %d), dropping",
                        index, len(data), self.piece_size(index))
            self.hash_failures += 1
            return False

        if not verify_piece(data, self.piece_hashes[index]):
            log.warning("piece %d failed hash check, dropping", index)
            self.hash_failures += 1
            return False

        self._open_part()
        self._fh.seek(index * self.piece_length)
        self._fh.write(data)
        self._fh.flush()
        self.have[index] = True
        return True

    def read_piece(self, index):
        """Bytes of a piece we have, or None if we don't have it."""
        if index < 0 or index >= self.num_pieces or not self.have[index]:
            return None
        if self._fh is None:
            if self.assembled:
                self._open_readonly(self.final_path)
            else:
                self._open_part()
        self._fh.seek(index * self.piece_length)
        return self._fh.read(self.piece_size(index))

    def read_block(self, index, begin, length):
        """Part of a piece, for answering request(index, begin, length) messages."""
        size = self.piece_size(index) if 0 <= index < self.num_pieces else 0
        if begin < 0 or length <= 0 or begin + length > size:
            return None
        piece = self.read_piece(index)
        if piece is None:
            return None
        return piece[begin:begin + length]

    @property
    def pieces_data(self):
        """
        dict-like read only view, so code doing `idx in asm.pieces_data` or
        `asm.pieces_data[idx]` keeps working now that pieces live on disk
        """
        return _PieceView(self)

    def clear_piece_data(self):
        # nothing is cached in memory anymore, just drop the file handle
        self.close()

    # ---------- seeding / resume ----------

    def seed_from(self, source_path):
        """
        Use an existing complete copy of the file (the seeder's original)
        as our data. Every piece is re-hashed, returns the number that matched.
        """
        if os.path.getsize(source_path) != self.file_length:
            raise ValueError(f"{source_path} is {os.path.getsize(source_path)} bytes, "
                             f"torrent says {self.file_length}")
        self._open_readonly(source_path)
        good = self._recheck_open_file()
        if good == self.num_pieces:
            self.assembled = True
            self.final_path = source_path
        log.info("seeding from %s, %d/%d pieces ok", source_path, good, self.num_pieces)
        return good

    def recheck(self):
        """
        Resume support: hash whatever is already in download_dir and mark
        the good pieces as had. Returns how many pieces we have.
        """
        if os.path.exists(self.final_path) and os.path.getsize(self.final_path) == self.file_length:
            self._open_readonly(self.final_path)
            if self._recheck_open_file() == self.num_pieces:
                self.assembled = True
                return self.num_pieces
            # final file is broken somehow, start over from the .part
            self.close()

        if not os.path.exists(self.part_path):
            return 0
        self._open_part()
        return self._recheck_open_file()

    def _recheck_open_file(self):
        good = 0
        for i in range(self.num_pieces):
            self._fh.seek(i * self.piece_length)
            data = self._fh.read(self.piece_size(i))
            self.have[i] = verify_piece(data, self.piece_hashes[i])
            good += self.have[i]
        return good

    # ---------- final file ----------

    def assemble(self, output_path=None):
        """
        Finish the download: check the whole file hash (if the torrent has
        one) and move the .part file to its final name.
        Returns the path of the finished file.
        """
        if not self.complete:
            missing = self.missing_pieces()
            raise RuntimeError(f"can't assemble, {len(missing)} pieces missing: {missing[:10]}")

        if output_path is None:
            output_path = self.final_path

        if self.assembled:
            # already done (or we're a seeder sitting on the original)
            if os.path.abspath(output_path) != os.path.abspath(self.final_path):
                raise RuntimeError(f"already assembled at {self.final_path}")
            return self.final_path

        self.close()  # windows won't rename a file that's still open

        size = os.path.getsize(self.part_path)
        if size != self.file_length:
            raise RuntimeError(f"{self.part_path} is {size} bytes, expected {self.file_length}")

        if self.file_hash is not None:
            actual = compute_file_hash(self.part_path)
            if actual != self.file_hash:
                # every piece passed but the whole file doesn't. shouldn't be possible
                # unless the disk/file got touched behind our back, so recheck everything
                log.error("whole file hash mismatch, rechecking pieces")
                self.recheck()
                raise RuntimeError("file hash mismatch after assembly")

        os.replace(self.part_path, output_path)
        self.final_path = output_path
        self.assembled = True
        log.info("assembled %s (%d bytes)", output_path, self.file_length)
        return output_path

    def verify_assembled_file(self, filepath, original_hash=None):
        """
        End-to-end check: sha256 of filepath against original_hash
        (or the file_hash from the torrent if not given).
        """
        expected = original_hash if original_hash is not None else self.file_hash
        if expected is None:
            raise ValueError("no hash to compare against")
        actual = compute_file_hash(filepath)
        if actual == expected:
            log.info("integrity check passed: %s", actual.hex())
            return True
        log.error("integrity check FAILED: expected %s got %s", expected.hex(), actual.hex())
        return False


class _PieceView:
    def __init__(self, asm):
        self._asm = asm

    def __contains__(self, index):
        return isinstance(index, int) and 0 <= index < self._asm.num_pieces and self._asm.have[index]

    def __getitem__(self, index):
        data = self._asm.read_piece(index)
        if data is None:
            raise KeyError(index)
        return data

    def get(self, index, default=None):
        data = self._asm.read_piece(index)
        return default if data is None else data

    def __len__(self):
        return self._asm.downloaded_pieces

    def keys(self):
        return [i for i, h in enumerate(self._asm.have) if h]

    def __iter__(self):
        return iter(self.keys())
