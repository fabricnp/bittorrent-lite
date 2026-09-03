# BitTorrent-Lite · P2P File Distribution

**CS-30003 · Coding Assignment 1**

A BitTorrent-lite peer-to-peer file distribution system built from the socket level in Python.

## Team

| Member | Name | Roll No |
|--------|------|---------|
| A | Bibek Mishra | 24052560 |
| B | Pravesh Basnet | 24052561 |
| C | Saurav Neupane | 24052564 |
| D | Sworup Bastola | 24052480 |

**Section:** 33

## Architecture

The system consists of three core components:

1. **Metainfo Generator** — Splits files into fixed-size pieces and produces per-piece SHA-256 hashes in a Bencode-like binary format.
2. **Tracker Server** — Maintains swarm membership and serves peer lists over a custom HTTP interface built on raw TCP sockets.
3. **Peer Client** — Implements a wire protocol (handshake, bitfield, have, request, piece, cancel) over TCP sockets for concurrent multi-peer file transfer.

## Features

- Configurable piece sizes with per-piece SHA-256 hash verification
- Tracker server with peer registration, announce, and swarm state management
- Full peer wire protocol: handshake, bitfield, have, request/piece/cancel
- Rarest-first piece selection with random fallback and request pipelining
- Multi-peer operation: ≥6 concurrent peers with async state machines (Python asyncio)
- Basic choking/unchoking mechanism for upload management and free-rider handling
- End-to-end integrity: SHA-256 verification of reassembled files

## Tech Stack

- **Python 3.12** — asyncio, hashlib, struct
- **matplotlib + pandas** — Experiment plots and analysis

## Project Structure

```
bittorrent-lite/
├── src/
│   ├── metainfo/        # Metainfo format, file splitter/assembler, hashing
│   ├── tracker/         # Tracker server (raw TCP HTTP, announce, swarm state)
│   ├── peer/            # Peer wire protocol (message framing, handshake, state machine)
│   └── strategy/        # Piece selection algorithms, choking logic
├── experiments/         # Experiment harness, configs, results
├── docs/               # Protocol specification, design docs
├── tests/              # Unit and integration tests
└── README.md
```

## Experiment

**Hypothesis:** Rarest-first piece selection reduces total swarm distribution time compared with random and sequential selection, particularly as swarm size increases.

- **File:** 100 MB, 256 KB piece size
- **Configurations:** Swarm size (6, 8 peers) × Seeder count (1, 2) × Strategy (rarest-first, random, sequential)
- **Runs:** 5 per configuration
- **DVs:** Total distribution time, per-peer completion time, piece availability variance
- **Free-rider experiment:** 2 free-rider peers to evaluate choking effectiveness

## License

MIT
