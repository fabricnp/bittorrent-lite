"""
bencode.py - bencode encoder/decoder for our metainfo files

Supported types:
  int    -> i<number>e          e.g. i42e
  bytes  -> <length>:<data>     e.g. 5:hello
  list   -> l<items>e           e.g. l5:helloi42ee
  dict   -> d<key><value>...e   keys are byte strings, sorted

str is accepted when encoding (gets utf-8 encoded) but decoding always
gives back bytes, the caller decides what is text and what is binary
(piece hashes are binary so we can't blindly decode everything).

The decoder is strict on purpose. Two peers must get the exact same
bytes when they re-encode the info dict, otherwise the info_hash
won't match and the handshake fails. So things like i03e, i-0e or
unsorted dict keys are rejected instead of "fixed".
"""


class BencodeError(ValueError):
    pass


def encode(obj):
    """Encode a python object into bencode bytes."""
    out = []
    _encode(obj, out)
    return b''.join(out)


def _encode(obj, out):
    # bool is a subclass of int, don't let True sneak in as i1e
    if isinstance(obj, bool):
        raise TypeError("can't bencode a bool")

    if isinstance(obj, int):
        out.append(b'i%de' % obj)

    elif isinstance(obj, (bytes, bytearray)):
        out.append(b'%d:' % len(obj))
        out.append(bytes(obj))

    elif isinstance(obj, str):
        _encode(obj.encode('utf-8'), out)

    elif isinstance(obj, (list, tuple)):
        out.append(b'l')
        for item in obj:
            _encode(item, out)
        out.append(b'e')

    elif isinstance(obj, dict):
        # normalise keys to bytes first, then sort by raw bytes
        items = []
        for k, v in obj.items():
            if isinstance(k, str):
                k = k.encode('utf-8')
            elif not isinstance(k, bytes):
                raise TypeError(f"dict keys must be bytes or str, got {type(k).__name__}")
            items.append((k, v))
        items.sort(key=lambda kv: kv[0])

        for i in range(1, len(items)):
            if items[i][0] == items[i - 1][0]:
                raise TypeError(f"duplicate key {items[i][0]!r}")

        out.append(b'd')
        for k, v in items:
            _encode(k, out)
            _encode(v, out)
        out.append(b'e')

    else:
        raise TypeError(f"can't bencode type {type(obj).__name__}")


def decode(data):
    """Decode bencode bytes. Raises BencodeError on bad / trailing input."""
    if isinstance(data, bytearray):
        data = bytes(data)
    if not isinstance(data, bytes):
        raise TypeError("decode() expects bytes")
    if not data:
        raise BencodeError("empty input")

    value, idx = _decode(data, 0)
    if idx != len(data):
        raise BencodeError(f"trailing data after position {idx}")
    return value


def _decode(data, idx):
    """returns (value, next_idx)"""
    if idx >= len(data):
        raise BencodeError("unexpected end of data")

    c = data[idx:idx + 1]

    if c == b'i':
        end = data.find(b'e', idx)
        if end == -1:
            raise BencodeError(f"unterminated int at {idx}")
        raw = data[idx + 1:end]
        _check_int(raw, idx)
        return int(raw), end + 1

    if c == b'l':
        items = []
        idx += 1
        while True:
            if idx >= len(data):
                raise BencodeError("unterminated list")
            if data[idx:idx + 1] == b'e':
                return items, idx + 1
            val, idx = _decode(data, idx)
            items.append(val)

    if c == b'd':
        d = {}
        last_key = None
        idx += 1
        while True:
            if idx >= len(data):
                raise BencodeError("unterminated dict")
            if data[idx:idx + 1] == b'e':
                return d, idx + 1

            if not data[idx:idx + 1].isdigit():
                raise BencodeError(f"dict key must be a string (pos {idx})")
            key, idx = _decode(data, idx)
            if last_key is not None and key <= last_key:
                raise BencodeError(f"dict keys not sorted / duplicated at {key!r}")
            last_key = key

            val, idx = _decode(data, idx)
            d[key] = val

    if c.isdigit():
        colon = data.find(b':', idx)
        if colon == -1:
            raise BencodeError(f"missing ':' in string length at {idx}")
        raw_len = data[idx:colon]
        if not raw_len.isdigit() or (len(raw_len) > 1 and raw_len[0:1] == b'0'):
            raise BencodeError(f"bad string length {raw_len!r} at {idx}")
        length = int(raw_len)
        start = colon + 1
        if start + length > len(data):
            raise BencodeError(f"string at {idx} runs past end of data")
        return data[start:start + length], start + length

    raise BencodeError(f"invalid token {c!r} at position {idx}")


def _check_int(raw, pos):
    # allowed: 0, 42, -7     not allowed: '', -, 007, -0
    body = raw[1:] if raw.startswith(b'-') else raw
    if not body or not body.isdigit():
        raise BencodeError(f"bad integer {raw!r} at {pos}")
    if body[0:1] == b'0' and (len(body) > 1 or raw.startswith(b'-')):
        raise BencodeError(f"non-canonical integer {raw!r} at {pos}")
