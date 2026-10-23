# Peer Wire Protocol

## 1. Overview

The P2P File Distribution system uses a custom peer wire protocol over TCP.

TCP provides a reliable byte stream, but it does not preserve application-level message boundaries. Therefore, the application protocol uses explicit message framing.

The Phase 2 implementation focuses on:

- TCP connection establishment
- Handshake exchange
- Message framing
- Handshake validation
- Peer state transitions

The protocol is designed so that additional messages such as BITFIELD, HAVE, REQUEST, PIECE, and CANCEL can be added in later phases.

---

## 2. Peer Identification

Each peer has two identifiers.

### Peer ID

The `peer_id` uniquely identifies an individual peer.

Example:

```text
peer-A
peer-B
```

### Swarm ID

The `swarm_id` identifies the file-sharing group that the peer belongs to.

Peers must have the same `swarm_id` to participate in the same swarm.

Example:

```text
swarm-001
```

Therefore:

```text
Peer ID  = Which peer am I?
Swarm ID = Which file-sharing group am I participating in?
```

---

## 3. Protocol Identifier

The protocol identifier used by this project is:

```text
P2P-FILE-DIST/1
```

This identifies the version of the custom peer wire protocol.

---

## 4. Message Types

Each protocol message has a numeric message type.

| Message | Type |
|---|---:|
| HANDSHAKE | 1 |
| BITFIELD | 2 |
| HAVE | 3 |
| REQUEST | 4 |
| PIECE | 5 |
| CANCEL | 6 |

These values are represented by the `MessageType` enum in `src/peer/protocol.py`.

Phase 2 implements the HANDSHAKE message. The remaining message types will be implemented in later phases.

---

## 5. Message Framing

Because TCP is a byte stream, every application message is framed as:

```text
+----------------+----------------+----------------+
| LENGTH         | MESSAGE TYPE   | PAYLOAD        |
+----------------+----------------+----------------+
```

### LENGTH

`LENGTH` is a 4-byte unsigned integer.

The integer is encoded using network byte order (big-endian).

The value represents the number of bytes following the LENGTH field:

```text
LENGTH = size of MESSAGE TYPE + PAYLOAD
```

Therefore:

```text
total frame size = 4 + LENGTH
```

For example, if the message type occupies 1 byte and the payload contains 10 bytes:

```text
LENGTH = 1 + 10
       = 11
```

---

## 6. Message Type Field

The `MESSAGE TYPE` field is exactly 1 byte.

Its value corresponds to the `MessageType` enum.

For example:

```text
HANDSHAKE = 1
BITFIELD  = 2
HAVE      = 3
REQUEST    = 4
PIECE      = 5
CANCEL     = 6
```

---

## 7. Generic Message Format

A complete message is therefore:

```text
4 bytes       1 byte          variable
+---------+-------------+----------------+
| LENGTH  | MESSAGE TYPE| PAYLOAD        |
+---------+-------------+----------------+
```

Example conceptually:

```text
[LENGTH][1][PAYLOAD]
```

where `1` represents a HANDSHAKE message.

---

## 8. Handshake Format

The handshake is exchanged immediately after a TCP connection is established.

The handshake contains:

1. Protocol ID
2. Swarm ID
3. Peer ID

The handshake payload uses length-prefixed UTF-8 strings.

Its structure is:

```text
+------------------+------------------+
| PROTOCOL ID      | SWARM ID         |
+------------------+------------------+
| PEER ID                             |
+-------------------------------------+
```

Each string is represented as:

```text
+----------------+----------------------+
| FIELD LENGTH   | FIELD DATA           |
+----------------+----------------------+
```

where:

- `FIELD LENGTH` = 2-byte unsigned integer
- byte order = big-endian
- `FIELD DATA` = UTF-8 encoded string

Therefore the handshake payload is:

```text
+----------------+----------------+
| Protocol Len   | Protocol Data  |
+----------------+----------------+
| Swarm Len      | Swarm Data     |
+----------------+----------------+
| Peer Len       | Peer Data      |
+----------------+----------------+
```

The complete handshake sent over TCP is:

```text
+----------+--------------+----------------------+
| 4 bytes  | 1 byte       | Handshake Payload    |
| LENGTH   | HANDSHAKE=1  |                      |
+----------+--------------+----------------------+
```

---

## 9. Handshake Validation

A received handshake is accepted only if all of the following conditions are satisfied:

### Protocol ID

The received protocol ID must equal:

```text
P2P-FILE-DIST/1
```

### Swarm ID

The received swarm ID must equal the local swarm ID.

A peer from another swarm must be rejected.

### Peer ID

The received peer ID must not be empty.

If any validation condition fails, the handshake is rejected.

---

## 10. Handshake Sequence

The client and server perform the following sequence.

### Client

```text
TCP CONNECT
     |
     v
HANDSHAKING
     |
     v
Create local Handshake
     |
     v
Encode Handshake
     |
     v
Frame HANDSHAKE message
     |
     v
Send message
     |
     v
Receive HANDSHAKE response
     |
     v
Decode response
     |
     v
Validate response
     |
     v
CONNECTED
```

### Server

```text
TCP ACCEPT
     |
     v
HANDSHAKING
     |
     v
Receive HANDSHAKE
     |
     v
Decode message
     |
     v
Validate handshake
     |
     v
Create local Handshake
     |
     v
Send HANDSHAKE response
     |
     v
CONNECTED
```

---

## 11. TCP Message Reception

TCP does not guarantee that one `recv()` call returns one complete application message.

For example, a message may arrive as:

```text
recv() -> first 3 bytes
recv() -> next 5 bytes
recv() -> remaining bytes
```

Therefore the implementation must use exact byte reception.

The `PeerConnection.receive_exactly()` method is responsible for repeatedly calling `recv()` until the requested number of bytes has been received.

The receiver should:

1. Read the fixed-size LENGTH field.
2. Decode the message length.
3. Read exactly the remaining number of bytes.
4. Reconstruct the complete frame.
5. Decode the message type and payload.

---

## 12. Error Handling

The implementation should reject malformed protocol data.

Examples include:

- Connection