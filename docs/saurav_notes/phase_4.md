# Phase 4 - Tracker, Swarm and Multi-Peer Download

## What was implemented

- A tracker that peers register with, query for other peers, and leave.
- A `PieceManager` that tracks local pieces, each peer's pieces, and in-flight requests.
- A rarest-first scheduler.
- A `PeerManager` (leecher) that keeps several peer connections open and downloads from all of them.
- Request pipelining: several REQUESTs outstanding per peer, size configurable.
- A `PeerServer` so one seeder process can serve many leechers at once.
- A small CLI (`src/swarm/node.py`) to run a seeder or a leecher against a tracker.

Phase 3 code is reused, not rewritten: handshake, framing, BITFIELD/HAVE/REQUEST/PIECE
payload helpers, SHA-256 verification, piece storage and assembly are all the existing functions.

## Files

| File | Role |
|---|---|
| `src/tracker/tracker.py` | `Tracker` (registry), `TrackerServer` (TCP front end), tracker CLI |
| `src/tracker/client.py` | `TrackerClient`: register / get_peers / unregister |
| `src/swarm/piece_manager.py` | `PieceManager`: availability, in-flight and downloaded state |
| `src/swarm/scheduler.py` | `select_rarest_piece` |
| `src/swarm/peer_manager.py` | `PeerManager`: multi-peer leecher with pipelining |
| `src/swarm/peer_server.py` | `PeerServer`: accepts many connections, serves pieces |
| `src/swarm/node.py` | CLI: `seed` and `leech` commands |
| `src/peer/peer.py` (modified) | `Peer` takes an optional `have` list so a seeder can advertise only some pieces |

## Architecture

```
            Tracker (TCP, JSON lines)
           register / get_peers / unregister
              ^                      |
              |                      v
 PeerServer(s) (seeders)       PeerManager (leecher)
   one Peer per connection       one Peer per connection + reader thread
   handshake -> BITFIELD         handshake -> read BITFIELD
   serve REQUEST with PIECE      PieceManager + rarest-first scheduler
                                 REQUEST pipeline, verify, store, HAVE
```

`Peer` (Phase 2/3) stays the per-connection object. `PeerManager` and `PeerServer` create one
`Peer` per TCP connection and call its existing `perform_client_handshake`,
`perform_server_handshake`, `run_seeder`, `send_framed_message` and `receive_framed_message`.

## How tracker communication works

The tracker keeps `swarm_id -> {peer_id -> (host, port)}` in memory behind a lock.
Each request is one TCP connection carrying one JSON line; the tracker replies with one JSON line.

```
{"type": "register",   "swarm_id": s, "peer_id": p, "host": h, "port": n}  ->  {"ok": true}
{"type": "get_peers",  "swarm_id": s, "peer_id": p}                        ->  {"ok": true, "peers": [...]}
{"type": "unregister", "swarm_id": s, "peer_id": p}                        ->  {"ok": true, "removed": bool}
```

- `get_peers` leaves out `peer_id` (the requester) if given.
- Swarms are isolated because every lookup is keyed by `swarm_id`; the peer handshake also
  still rejects a mismatched `swarm_id`.
- Bad input (invalid JSON, unknown type, bad port, empty ids) gets `{"ok": false, "error": ...}`
  and the tracker keeps running.

## How piece availability is maintained

`PieceManager` holds:

- `_have`: which pieces we have downloaded and verified.
- `_peer_pieces`: for each connected peer, its bitfield.
- `_in_flight`: piece index -> the peer it was requested from.

Updates:

- BITFIELD from a new peer -> `add_peer`.
- HAVE from a peer -> `update_peer_have`.
- Peer disconnects -> `remove_peer` (also releases that peer's in-flight pieces).
- Verified PIECE -> `mark_downloaded`.

`availability()` counts, per piece, how many connected peers have it.
`requestable_pieces(peer)` returns pieces that peer has which we do not have and have not requested.
These two are everything the scheduler needs.

## How rarest-first works

`select_rarest_piece(piece_manager, peer_id)`:

1. Take `requestable_pieces(peer_id)` (already excludes downloaded and in-flight pieces).
2. Pick the piece with the smallest `(availability, index)` pair.

Lowest availability wins; equal availability goes to the lowest index, so the result is deterministic.
It returns `None` if the peer has nothing useful. Because each peer's pipeline is filled with the
rarest piece that peer can supply, rare pieces are requested before common ones.

## How multi-peer downloading works

`PeerManager.add_peer(host, port)`: TCP connect, handshake, read the BITFIELD, register the
peer in `PieceManager`, start a reader thread for that connection.
`connect_to_peers(peers)` does this for tracker entries until `max_peers` (default 6) is reached.
The design is limited only by `max_peers`; tests connect 6 peers at once and verify the cap.

`start()` begins requesting. Call it after connecting so the scheduler sees every peer's BITFIELD
before the first choice. Peers added later are filled immediately.

Each reader thread handles HAVE and PIECE messages for its peer. All shared state is guarded by one
`RLock`. On a PIECE message the leecher checks it was requested from that peer, verifies SHA-256,
stores it with `write_piece`, marks it downloaded, sends HAVE to all connected peers, then refills
the pipeline. A peer that sends a corrupt or unrequested piece, or disconnects, is dropped; its
in-flight pieces are released and the remaining peers pick them up.

`wait_until_complete(timeout)` returns True only when every piece is downloaded; it returns False on
timeout or when all peers have gone.

## How request pipelining works

For each peer, `_fill` sends REQUESTs until `pipeline_size` are outstanding
(`PieceManager.in_flight_count(peer) == pipeline_size`). `mark_in_flight` is called before sending, and
refuses a piece that is already in flight, so the same piece is never requested twice at the same time,
even across peers. Each arriving PIECE frees one slot and triggers another REQUEST. `pipeline_size` is a
`PeerManager` argument (CLI: `--pipeline`, default 5).

## Running it

```
python -m src.tracker.tracker --port 7000
python -m src.swarm.node seed  --peer-id seed1 --swarm-id demo --metainfo f.json --file f.bin --tracker-port 7000
python -m src.swarm.node leech --peer-id me    --swarm-id demo --metainfo f.json --tracker-port 7000 --output out.bin
```

## Tests

New tests: 100 (tracker 28, piece manager 23, scheduler 12, peer server 8, peer manager 29).
Full suite: **266 passed** (166 existing Phase 1-3 tests unchanged + 100 new), repeated runs stable.
Run with `python -m pytest` from the project root.

| Test file | Covers |
|---|---|
| `test_tracker.py` | register / get / unregister, swarm isolation, re-register, exclude, invalid input, TCP server + client, bad requests, concurrent registrations |
| `test_piece_manager.py` | local pieces, availability, HAVE, peer removal, in-flight rules, requestable pieces |
| `test_scheduler.py` | rarest choice, tie-breaking, ignoring downloaded/in-flight, HAVE and disconnect effects, full rarest-first order |
| `test_peer_server.py` | full and partial BITFIELD, serving a piece, refusing an unavailable piece, 6 simultaneous connections, wrong swarm |
| `test_peer_manager.py` | 6 concurrent peers, `max_peers`, multi-peer integration with 6 partial peers, rarest-first request order on the wire, pipelining (3 outstanding, configurable, refill, no duplicates), disconnect recovery, corrupt piece, unrequested piece, tracker integration |

Manual end-to-end (CLI): tracker + 3 seeders + 1 leecher, 3,000,000 byte file, 16 KiB pieces: output
identical to the source, pieces spread across all three seeders. In-process stress run: 40 MiB,
256 KiB pieces, 6 seeders, pipeline 10: identical output, all six peers contributed.

## Limitations

- The leecher does not upload or register with the tracker; only `PeerServer` processes serve pieces.
  Nothing a leecher has downloaded is offered to others yet.
- `PeerServer` sends its BITFIELD once on connect; it does not send HAVE updates later.
- No choking, tit-for-tat, or endgame mode. CANCEL is defined in the protocol but unused.
- The tracker has no peer expiry or heartbeat: a crashed peer stays registered until it unregisters.
- The leecher always starts with no pieces (no resume from an existing pieces directory).
- Pipeline depth is per peer; there is no global cap on outstanding requests.
- If no connected peer has some piece, `wait_until_complete` runs until its timeout.
