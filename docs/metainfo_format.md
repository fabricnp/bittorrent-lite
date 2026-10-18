# Metainfo (.torrent) format

Owner: Bibek (Member A). This is the part of the spec covering the
torrent file itself; B/D can merge it into protocol_spec.md.

## Encoding

Files are bencoded, same idea as real BitTorrent:

| type   | encoding            | example        |
|--------|---------------------|----------------|
| int    | `i<n>e`             | `i42e`         |
| bytes  | `<len>:<data>`      | `4:spam`       |
| list   | `l<items>e`         | `l4:spami1ee`  |
| dict   | `d<k><v>...e`       | `d3:agei3ee`   |

Our decoder is strict: no leading zeros (`i03e`), no `i-0e`, dict keys
must be byte strings in sorted order with no duplicates, and nothing
is allowed after the top-level value. The reason is the info_hash: if
two peers could decode slightly different input and re-encode it
differently, they would compute different info hashes for the same
torrent.

## Fields

```
{
  tracker_url:   "http://127.0.0.1:8000"
  created_by:    "bittorrent-lite"
  creation_date: 1760000000
  info: {
    file_name:    "test100.bin"
    file_length:  104857600
    piece_length: 262144
    pieces:       <num_pieces * 32 bytes>
    file_hash:    <32 bytes>
  }
}
```

- `pieces` is the SHA-256 of each piece, joined together. Piece `i` covers
  bytes `[i*piece_length, min((i+1)*piece_length, file_length))`, so
  only the last piece can be shorter.
- `num_pieces = ceil(file_length / piece_length)`; a parser rejects the
  file if `len(pieces) != num_pieces * 32`.
- `file_hash` is the SHA-256 of the whole file. A leecher uses it for the
  end-to-end integrity check after reassembly. (Torrents without it still
  parse, the check is just skipped.)
- `file_name` has to be a plain name with no directories, so a torrent can't
  make a peer write outside its download folder.

For the experiment file (100 MB, 256 KB pieces) that is 400 pieces and
a 12,800 byte `pieces` field.

## info_hash

`info_hash = SHA-256(bencode(info))`, 32 bytes. Sent in the handshake
and used by the tracker as the swarm key (hex encoded in the announce
query). Only `info` is hashed, so changing the tracker url or regenerating
the file later gives the same info_hash as long as the file and piece
size are the same.

## On disk while downloading

The assembler writes every verified piece at its offset in
`<download_dir>/<file_name>.part`, which is pre-sized to `file_length`.
When all pieces are in, the whole file hash is checked and the `.part`
file is renamed to `file_name`. If a peer restarts, `recheck()` hashes
the existing `.part` and keeps the good pieces.

A piece is rejected (and counted in `hash_failures`) if its length is
wrong for its index or its hash doesn't match.

## CLI

```
python -m src.metainfo mkfile data/test100.bin 100M --seed 42
python -m src.metainfo create data/test100.bin -t http://127.0.0.1:8000 -p 256K
python -m src.metainfo info data/test100.torrent
python -m src.metainfo verify data/test100.torrent downloads/peer3/test100.bin
```
