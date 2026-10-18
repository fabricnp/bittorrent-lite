"""
command line helper for torrent files

    python -m src.metainfo create data/test100.bin --tracker http://127.0.0.1:8000 --piece-size 256K
    python -m src.metainfo info data/test100.torrent
    python -m src.metainfo verify data/test100.torrent downloads/peer3/test100.bin
    python -m src.metainfo mkfile data/test100.bin 100M --seed 42
"""

import argparse
import logging
import os
import random
import sys

from .metainfo import generate, parse, MetainfoError
from .file_splitter import compute_file_hash, verify_piece, piece_size


def parse_size(text):
    """'256K' -> 262144, '100M' -> 104857600, '4096' -> 4096"""
    text = text.strip().upper()
    mult = 1
    if text.endswith('B'):
        text = text[:-1]
    if text and text[-1] in 'KMG':
        mult = 1024 ** ('KMG'.index(text[-1]) + 1)
        text = text[:-1]
    try:
        val = int(text) * mult
    except ValueError:
        raise argparse.ArgumentTypeError(f"bad size: {text!r}")
    if val <= 0:
        raise argparse.ArgumentTypeError("size must be > 0")
    return val


def human(n):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f"{n:.0f} {unit}" if unit == 'B' else f"{n:.1f} {unit}"
        n /= 1024


def cmd_create(args):
    out, ih = generate(args.file, args.tracker, piece_length=args.piece_size, output_path=args.output)
    t = parse(out)
    print(f"wrote {out}")
    print(f"  {t['num_pieces']} pieces x {human(t['piece_length'])}")
    print(f"  info_hash {ih}")


def cmd_info(args):
    t = parse(args.torrent)
    print(f"file:        {t['file_name']}")
    print(f"size:        {t['file_length']} bytes ({human(t['file_length'])})")
    print(f"piece size:  {t['piece_length']} bytes ({human(t['piece_length'])})")
    print(f"pieces:      {t['num_pieces']} (last one {piece_size(t['num_pieces'] - 1, t['file_length'], t['piece_length'])} bytes)")
    print(f"tracker:     {t['tracker_url']}")
    print(f"info_hash:   {t['info_hash_hex']}")
    print(f"file sha256: {t['file_hash'].hex() if t['file_hash'] else '(not stored)'}")
    if args.pieces:
        for i, h in enumerate(t['piece_hashes']):
            print(f"  {i:5d}  {h.hex()}")


def cmd_verify(args):
    t = parse(args.torrent)
    size = os.path.getsize(args.file)
    if size != t['file_length']:
        print(f"FAIL: size {size}, expected {t['file_length']}")
        return 1

    bad = []
    with open(args.file, 'rb') as f:
        for i, h in enumerate(t['piece_hashes']):
            data = f.read(t['piece_length'])
            if not verify_piece(data, h):
                bad.append(i)

    if bad:
        print(f"FAIL: {len(bad)}/{t['num_pieces']} pieces bad: {bad[:20]}{' ...' if len(bad) > 20 else ''}")
        return 1

    if t['file_hash'] is not None and compute_file_hash(args.file) != t['file_hash']:
        print("FAIL: all pieces ok but whole file hash differs")
        return 1

    print(f"OK: {t['num_pieces']} pieces verified, file sha256 matches")
    return 0


def cmd_mkfile(args):
    # random test file, seeded so every run of the experiment uses identical data
    rng = random.Random(args.seed)
    remaining = args.size
    os.makedirs(os.path.dirname(os.path.abspath(args.path)), exist_ok=True)
    with open(args.path, 'wb') as f:
        while remaining > 0:
            n = min(remaining, 1024 * 1024)
            f.write(rng.randbytes(n))
            remaining -= n
    print(f"wrote {args.path} ({human(args.size)}, seed {args.seed})")


def main(argv=None):
    p = argparse.ArgumentParser(prog='python -m src.metainfo')
    p.add_argument('-v', '--verbose', action='store_true')
    sub = p.add_subparsers(dest='cmd', required=True)

    c = sub.add_parser('create', help='make a .torrent for a file')
    c.add_argument('file')
    c.add_argument('--tracker', '-t', required=True, help='e.g. http://127.0.0.1:8000')
    c.add_argument('--piece-size', '-p', type=parse_size, default='256K')
    c.add_argument('--output', '-o')
    c.set_defaults(func=cmd_create)

    i = sub.add_parser('info', help='show what is inside a .torrent')
    i.add_argument('torrent')
    i.add_argument('--pieces', action='store_true', help='also list every piece hash')
    i.set_defaults(func=cmd_info)

    v = sub.add_parser('verify', help='check a downloaded file against a .torrent')
    v.add_argument('torrent')
    v.add_argument('file')
    v.set_defaults(func=cmd_verify)

    m = sub.add_parser('mkfile', help='make a random test file')
    m.add_argument('path')
    m.add_argument('size', type=parse_size)
    m.add_argument('--seed', type=int, default=0)
    m.set_defaults(func=cmd_mkfile)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format='%(levelname)s %(name)s: %(message)s')
    try:
        return args.func(args) or 0
    except (MetainfoError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
