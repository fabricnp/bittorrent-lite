# Metainfo used by the swarm-node demo

The current swarm-node path stores metainfo as JSON. It is a small course-project format, not a standard BitTorrent .torrent file.

## Fields

~~~json
{
  "file_name": "demo.bin",
  "file_size": 65536,
  "piece_size": 4096,
  "piece_count": 16,
  "piece_hashes": [
    "lowercase SHA-256 hex digest for piece 0",
    "..."
  ]
}
~~~

- file_name is the suggested output name. Validation rejects path components such as ../outside.bin.
- file_size is the original file length in bytes.
- piece_size is the normal size of each piece in bytes.
- piece_count must equal the ceiling of file_size divided by piece_size.
- piece_hashes contains one SHA-256 digest per piece, in piece order. Each digest is stored as 64 lowercase hexadecimal characters.

The final piece may be shorter than piece_size. Its length is the number of bytes left after all earlier full pieces.

## Creating and reading metainfo

create_metainfo reads the source file in piece_size chunks and returns a Metainfo object. save_metainfo writes that object as JSON; load_metainfo reads the JSON and validates it.

~~~python
from src.metainfo.metainfo import create_metainfo, save_metainfo

meta = create_metainfo("demo.bin", piece_size=4096)
save_metainfo(meta, "demo.json")
~~~

The swarm-node commands read this JSON through load_metainfo. The tracker swarm identifier is supplied separately with --swarm-id; it is not a field in this JSON format.

## Checking a received piece

The receiver hashes the piece bytes with SHA-256 and compares the result with the digest at the same index. A piece with the wrong length or digest must not be accepted.

In the swarm-node path, piece hashes are hexadecimal strings and verification is handled by src/peer/pieces.py. The asyncio peer client uses the same JSON metainfo through the `generate` and `parse` helpers, then passes the raw digest bytes to `FileAssembler`. This JSON format does not store a whole-file hash, so that optional final check is skipped.

## Related code

- src/metainfo/metainfo.py — Metainfo, validation, JSON save/load, and piece hashing
- src/peer/pieces.py — per-piece verification and storage used by the swarm-node path
- src/swarm/peer_manager.py — requests and receives pieces
- src/metainfo/assembler.py — writes verified pieces to disk for the asyncio peer client
- src/metainfo/file_splitter.py — piece sizing and file hashing helpers used by the experiment client

The `generate` and `parse` helpers are used by the experiment runner and asyncio peer client. The swarm-node commands use `create_metainfo`, `save_metainfo`, and `load_metainfo` directly.
