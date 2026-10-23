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

## 2. Peer wire protocol

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

## 3. Piece selection and choking (Member D)

*To be written by Member D* (`src/strategy/piece_selector.py`, `src/strategy/choking.py`).
