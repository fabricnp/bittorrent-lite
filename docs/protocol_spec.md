# Protocol Specification

Owners: Pravesh (Member B) + Sworup (Member D).
Torrent file format is in [metainfo_format.md](metainfo_format.md) (Member A).

There are two protocols in the system:

1. **Tracker protocol** - peer <-> tracker, HTTP/1.1 over TCP, one request per connection.
2. **Peer wire protocol** - peer <-> peer, binary messages over a long lived TCP connection.

---

## 1. Tracker protocol (Member B)

Code: `src/tracker/` (`tracker.py` server, `http_request.py` parser, `swarm.py` state).

The tracker is a small HTTP server written on raw asyncio TCP sockets. No
http library is used: we read bytes until the blank line `\r\n\r\n`, split
the request line and headers ourselves and percent-decode the query string
ourselves.

### 1.1 Request format

```
GET /announce?info_hash=<64 hex>&peer_id=<id>&port=<n>&event=started&left=<n> HTTP/1.1\r\n
Host: 127.0.0.1:8000\r\n
Connection: close\r\n
\r\n
```

- Only `GET` is accepted (anything else -> 405 with `Allow: GET`).
- `HTTP/1.0` and `HTTP/1.1` are accepted, other versions -> 505.
- The request can arrive in several TCP segments, the tracker keeps reading
  until it sees `\r\n\r\n`.
- Limits: request line max 2048 bytes (414), whole head max 8192 bytes (431).
- The tracker always answers with `Connection: close` and closes the socket.

### 1.2 `GET /announce`

| param        | required | meaning |
|--------------|----------|---------|
| `info_hash`  | yes | SHA-256 of the bencoded info dict, as 64 hex chars (case does not matter) |
| `peer_id`    | yes | peer's id, 1-64 chars |
| `port`       | yes | port the peer listens on for other peers, 1-65535 |
| `event`      | no  | `started`, `completed`, `stopped` or empty (regular re-announce) |
| `uploaded`   | no  | total bytes uploaded so far |
| `downloaded` | no  | total bytes downloaded so far |
| `left`       | no  | bytes the peer still needs. `0` means the peer is a seeder |
| `numwant`    | no  | how many peers to return, default 50, max 200 |

All numbers must be non-negative integers. The peer's IP is taken from the
TCP connection itself, not from the request.

What the tracker does:

- **started / empty** - add the peer to the swarm for `info_hash` (create the
  swarm if needed) or update its ip/port/stats and `last_seen` time.
- **completed** - same, and mark the peer as a seeder. Counted in `downloaded` of scrape.
- **stopped** - remove the peer. If the swarm is now empty it is deleted.
- If `left` is given, `left == 0` -> seeder, otherwise leecher.

Response (200, JSON body):

```json
{
  "interval": 30,
  "min interval": 15,
  "complete": 1,
  "incomplete": 2,
  "peers": [
    {"peer_id": "seeder-0", "ip": "127.0.0.1", "port": 7001},
    {"peer_id": "leecher-1", "ip": "127.0.0.1", "port": 7003}
  ]
}
```

- `interval` - seconds until the peer should announce again.
- `complete` / `incomplete` - number of seeders / leechers in the swarm.
- `peers` - other peers in the swarm, never includes the peer asking. If
  there are more than `numwant` peers, a random sample is returned so every
  peer doesn't get the same list.

### 1.3 `GET /scrape?info_hash=<64 hex>`

```json
{"info_hash": "ab..", "complete": 2, "incomplete": 0, "downloaded": 1, "peers": 2}
```

`downloaded` = how many peers have sent `event=completed`. Unknown swarm -> all zeros.

### 1.4 `GET /stats`

Debug endpoint, lists every swarm with its peer ids, seeder/leecher counts,
plus `total_announces` and `bad_requests`.

### 1.5 Errors

Every error is JSON `{"failure reason": "<text>"}` with one of these codes:

| code | when |
|------|------|
| 400 | broken request line / header, bad percent encoding, missing or bad param |
| 404 | unknown path |
| 405 | method is not GET |
| 414 | request line longer than 2048 bytes |
| 431 | headers bigger than 8192 bytes |
| 500 | unexpected error inside the tracker |
| 505 | HTTP version is not 1.0 / 1.1 |

### 1.6 Dead peers

Every peer has a `last_seen` time. A background task runs every 30 s and
removes peers that have not announced for 120 s (peer crashed or lost
network without sending `stopped`). Swarms that become empty are deleted.
`/scrape` also cleans its swarm before answering.

Start the tracker:

```
python -m src.tracker --host 127.0.0.1 --port 8000 --interval 30 --timeout 120
```

---

## 2. Peer wire protocol (Member C)

Code: `src/peer/protocol.py`. All integers are big-endian.

### 2.1 Handshake (88 bytes, sent first by both sides)

| bytes | field |
|-------|-------|
| 1     | `pstrlen` = 15 |
| 15    | `pstr` = `BitTorrent-lite` |
| 8     | reserved, all zero |
| 32    | `info_hash` (raw SHA-256) |
| 32    | `peer_id`, utf-8, padded with `\x00` |

If the `info_hash` doesn't match ours, the connection is closed.

### 2.2 Messages

After the handshake every message is `<4 byte length><1 byte id><payload>`.
`length` counts the id + payload. A length of 0 (no id) is a keep-alive.

| id | name           | payload |
|----|----------------|---------|
| 0  | choke          | - |
| 1  | unchoke        | - |
| 2  | interested     | - |
| 3  | not_interested | - |
| 4  | have           | `<4 piece index>` |
| 5  | bitfield       | one bit per piece, high bit of byte 0 = piece 0 |
| 6  | request        | `<4 index><4 begin><4 length>` |
| 7  | piece          | `<4 index><4 begin><data>` |
| 8  | cancel         | `<4 index><4 begin><4 length>` |

Normal flow: handshake -> bitfield -> interested -> (wait for unchoke) ->
request (up to 5 outstanding) -> piece -> verify SHA-256 -> have to everyone.

---

## 3. Piece Selection and Choking (Member D)

### 3.1 Piece Selection Strategies

Code: `src/strategy/piece_selector.py`

Three strategies are implemented to control which piece a peer requests next when downloading from another peer:

#### 3.1.1 Rarest-First Selection

**Algorithm**: Among all pieces that:
- The peer we're connected to has
- We don't have yet
- Are not already requested (pending)

Pick the one that the fewest connected peers have (lowest availability count).

**Purpose**: Distributes rare pieces quickly across the swarm. This improves overall swarm health because:
1. Rare pieces become more common faster
2. Peers become useful uploaders sooner (they have something others want)
3. The swarm is more resilient to peer departures

**Tie-breaking**: When multiple pieces have the same availability, pick randomly to avoid all peers requesting the same piece simultaneously.

**Implementation**:
```
Class: RarestFirst
- update_availability(bitfield) - track piece counts when peer connects
- remove_peer_availability(bitfield) - update counts when peer disconnects
- select(missing, peer_bitfield, pending) - returns piece index or None
```

**Example**: If pieces 0,1,2 are available from 5,3,3 peers respectively and we need all three, request piece 1 first.

#### 3.1.2 Random Selection

**Algorithm**: Pick a random piece from the set of pieces that satisfy:
- The peer has it
- We don't have it yet
- Not already pending

**Purpose**: Baseline comparison strategy. Requires no availability tracking, no coordination. Useful for:
- Validating that rarest-first actually helps
- Low-overhead operation when piece availability doesn't matter

**Implementation**:
```
Class: RandomSelect
- select(missing, peer_bitfield, pending) - returns random choice or None
- update_availability/remove_peer_availability - no-ops
```

#### 3.1.3 Sequential Selection

**Algorithm**: Among available pieces, always pick the lowest index first (0, then 1, then 2...).

**Purpose**: Baseline comparison. Represents naive download order. Expected to perform worst because:
- All peers want the same pieces at the same time
- High contention on early pieces
- Late pieces remain rare for longest

**Implementation**:
```
Class: SequentialSelect
- select(missing, peer_bitfield, pending) - returns min(candidates) or None
- update_availability/remove_peer_availability - no-ops
```

### 3.2 Integration with Peer Connections

Each `PeerConnection` has a reference to a `piece_selector` instance. When the connection is:
- Not choked by the remote peer (`peer_choking == False`)
- Interested in the remote peer (`am_interested == True`)
- Has fewer than `max_pending` (default 5) outstanding requests

The connection calls:
```python
piece_idx = self.piece_selector.select(
    missing=self.assembler.missing_pieces(),
    peer_bitfield=self.peer_bitfield,
    pending_requests=self.pending_requests
)
```

If a piece is selected, a REQUEST message is sent and the index is added to `pending_requests`.

### 3.3 Choking Algorithm

Code: `src/strategy/choking.py`

**Purpose**: Limit upload bandwidth to peers who reciprocate (upload to us). Discourages free-riding and rewards peers who contribute.

#### 3.3.1 Algorithm Parameters

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `max_unchoked` | 4 | Max number of regular unchoked peers |
| `eval_interval` | 10s | How often to re-evaluate who to unchoke |
| `optimistic_interval` | 30s | How often to unchoke a random choked peer |

#### 3.3.2 Regular Unchoking

Every `eval_interval` seconds:

1. **Collect interested peers**: Only peers with `peer_interested == True` are candidates
2. **Sort by contribution**: Rank by how many bytes they uploaded to us in the last interval
3. **Unchoke top N**: The `max_unchoked` most generous peers are unchoked
4. **Choke the rest**: Previously unchoked peers not in top N are choked
5. **Reset counters**: Upload byte counts are zeroed for the next interval

**Free-rider detection**: Peers with zero upload in the interval are logged as potential free-riders.

#### 3.3.3 Optimistic Unchoking

Every `optimistic_interval` seconds (separate from regular evaluation):

1. **Pick a random choked peer** who is interested in us
2. **Unchoke them** even though they haven't uploaded recently
3. **Choke the previous optimistic peer** (if they're not in the regular unchoked set)

**Purpose**: 
- Give new peers a chance to prove themselves
- Discover peers with better connections than current unchoked set
- Prevent starvation of unlucky peers

#### 3.3.4 Integration

The `ChokingManager` is queried by each peer connection:

```python
to_unchoke, to_choke = choking_manager.evaluate(connected_peers)

for peer_id in to_unchoke:
    connection.send_unchoke()

for peer_id in to_choke:
    connection.send_choke()
```

When a peer uploads data to us:
```python
choking_manager.record_upload(peer_id, num_bytes)
```

#### 3.3.5 Seeder Behavior

Seeders (peers with all pieces) unchoke all interested peers immediately since they have no download incentive - they're purely altruistic.

### 3.4 Interaction Between Selection and Choking

1. **Choking affects download**: If we are choked by a peer, we cannot request pieces from them
2. **Selection affects upload**: Which pieces we have determines if other peers are interested in us
3. **Rarest-first helps choking**: By acquiring rare pieces quickly, we become valuable to more peers, which increases our upload opportunities

### 3.5 Request Pipeline

To maintain efficiency despite network round-trip time, up to **5 requests** per peer can be outstanding simultaneously (pipelined). When a PIECE arrives:
- Remove that index from `pending_requests`
- Immediately request another piece if available

Without pipelining, each piece would suffer a full RTT of idle time.

### 3.6 Piece Verification

Every received piece is SHA-256 verified before:
- Accepting it into `have` bitfield
- Broadcasting HAVE messages to other peers
- Allowing the assembler to write it to disk

Bad pieces are silently dropped and not counted against the sender's upload stats.

### 3.7 Endgame Mode

Not implemented. In a production system, when only a few pieces remain, all peers would request those pieces from multiple sources and cancel duplicates when the first arrives.

---

## 4. Experimental Validation (Member D)

### 4.1 Hypothesis

Rarest-first piece selection reduces total swarm distribution time compared with random and sequential selection, particularly as swarm size increases.

### 4.2 Experiment Design

**Fixed Parameters**:
- File size: 100 MB
- Piece size: 256 KB
- Rate limit: 5 MB/s per peer (token bucket)

**Independent Variables**:
- Piece selection strategy: rarest-first, random, sequential
- Swarm size: 6 and 8 peers
- Seeder count: 1 and 2

**Dependent Variables**:
- Total distribution time (max completion time across all leechers)
- Per-peer completion time
- Piece availability variance over time

**Repetitions**: 5 runs per configuration for statistical significance

**Free-Rider Experiment**: Separate runs with 2 non-uploading peers to validate choking effectiveness.

### 4.3 Implementation

Code: `experiments/experiment.py`, `experiments/plot_results.py`

The experiment harness:
1. Creates a test file with reproducible random data
2. Generates the metainfo file
3. Starts the tracker
4. Launches N seeders and M leechers with the specified strategy
5. Records completion times and transfer statistics
6. Saves results as JSON

Results are analyzed and plotted as:
- Box plots of distribution time per strategy
- Per-peer completion time comparison
- Free-rider impact visualization

### 4.4 Running Experiments

```bash
# Run full experiment suite
python -m experiments.experiment

# Generate plots from results
python -m experiments.plot_results
```

Results are saved to `experiments/results/`:
- `results.json` - raw data
- `distribution_time.png` - main hypothesis test
- `per_peer_completion.png` - individual peer performance
- `free_rider_impact.png` - choking effectiveness
- `summary.csv` - statistical summary

---

## 5. System Architecture

```
┌─────────────┐
│   Tracker   │  HTTP/TCP server, manages swarm membership
└──────┬──────┘
       │ announce/peer-list
       │
   ┌───┴────────────────┐
   │                    │
┌──▼───┐            ┌───▼──┐
│Peer A│◄──TCP─────►│Peer B│  Wire protocol: handshake, bitfield,
└──┬───┘            └───┬──┘  request, piece, choke, unchoke
   │                    │
   │  ┌──────────────┐  │
   └─►│ Piece Store  │◄─┘     FileAssembler: verify & write pieces
      └──────────────┘
```

Each peer has:
- **PeerClient**: orchestrates tracker communication and peer connections
- **PeerConnection**: manages one TCP connection to another peer
- **FileAssembler**: validates and stores received pieces
- **PieceSelector**: chooses which piece to request next
- **ChokingManager**: decides which peers to unchoke

---

## 6. Testing

Unit tests: `tests/test_*.py`

Key test coverage:
- Protocol message encoding/decoding
- Piece selection algorithm behavior
- Choking manager state transitions
- Tracker announce/scrape
- Multi-peer transfer scenarios

Run tests:
```bash
pytest tests/ -v
```

---

## 7. Future Work

Not implemented (outside project scope):
- Endgame mode (parallel requests for final pieces)
- NAT traversal / DHT
- Encryption
- Super-seeding mode
- Piece reordering for streaming
- UDP tracker protocol
- Magnet links
