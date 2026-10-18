"""
metainfo package - torrent file creation/parsing, splitting files into
pieces, piece hashing and reassembly. (Member A)
"""

from .metainfo import generate, parse, parse_bytes, compute_info_hash, MetainfoError
from .file_splitter import (split_file, get_piece_data, verify_piece, compute_file_hash,
                            piece_size, num_pieces_for, DEFAULT_PIECE_LENGTH)
from .assembler import FileAssembler
from . import bencode
