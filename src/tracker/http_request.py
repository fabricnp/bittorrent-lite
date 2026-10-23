# http_request.py
# Member B (Pravesh) - reading and parsing the HTTP request by hand.
#
# We are not allowed to use any http library, so this file does the
# parsing ourselves. A request from a peer looks like this:
#
#   GET /announce?info_hash=ab12..&peer_id=leecher-0&port=7002 HTTP/1.1\r\n
#   Host: 127.0.0.1:8000\r\n
#   Connection: close\r\n
#   \r\n
#
# First line = request line, then headers, then an empty line.
# A GET has no body so we stop reading at the empty line.

import asyncio


# limits so a bad client cant make us use too much memory
MAX_REQUEST_LINE = 2048   # bytes, longer than this -> 414 URI Too Long
MAX_HEADER_BYTES = 8192   # whole head (request line + headers) -> 431


class BadRequest(Exception):
    # raised when the request is broken. status is the http code we
    # should send back (400, 414, 431, 505 ...)
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class Request:
    # simple holder for a parsed request
    def __init__(self, method, path, query, version, headers):
        self.method = method
        self.path = path        # just the path part, like "/announce"
        self.query = query      # dict of query params (already decoded)
        self.version = version  # "HTTP/1.1"
        self.headers = headers  # dict, header names in lowercase


async def read_request_head(reader, timeout=10):
    # TCP is a stream, so one read() might give us only half the request
    # (or two requests stuck together). so we keep reading until we see
    # the blank line "\r\n\r\n" that ends the headers.
    data = b''
    while True:
        if b'\r\n\r\n' in data:
            break

        if len(data) > MAX_HEADER_BYTES:
            # check if its the first line that is too long or the headers
            first_line_end = data.find(b'\r\n')
            if first_line_end == -1 or first_line_end > MAX_REQUEST_LINE:
                raise BadRequest(414, 'request line too long')
            raise BadRequest(431, 'headers too large')

        chunk = await asyncio.wait_for(reader.read(1024), timeout=timeout)
        if not chunk:
            # client closed before sending the full request
            if data == b'':
                return None
            raise BadRequest(400, 'connection closed in the middle of request')
        data = data + chunk

    head = data.split(b'\r\n\r\n', 1)[0]
    if len(head) > MAX_HEADER_BYTES:
        raise BadRequest(431, 'headers too large')
    return head


def parse_request(head):
    # head is the bytes before the blank line
    try:
        text = head.decode('ascii')
    except UnicodeDecodeError:
        raise BadRequest(400, 'request must be ascii')

    lines = text.split('\r\n')
    request_line = lines[0]

    if len(request_line) > MAX_REQUEST_LINE:
        raise BadRequest(414, 'request line too long')

    parts = request_line.split(' ')
    if len(parts) != 3:
        raise BadRequest(400, 'bad request line')

    method = parts[0]
    target = parts[1]
    version = parts[2]

    if not version.startswith('HTTP/'):
        raise BadRequest(400, 'bad http version')
    if version != 'HTTP/1.1' and version != 'HTTP/1.0':
        raise BadRequest(505, 'only HTTP/1.0 and HTTP/1.1 supported')

    # split "/announce?a=1&b=2" into path and query string
    if '?' in target:
        path, query_string = target.split('?', 1)
    else:
        path = target
        query_string = ''

    query = parse_query_string(query_string)

    # headers: "Name: value"
    headers = {}
    for line in lines[1:]:
        if line == '':
            continue
        if ':' not in line:
            raise BadRequest(400, 'bad header line')
        name, value = line.split(':', 1)
        headers[name.strip().lower()] = value.strip()

    return Request(method, path, query, version, headers)


def parse_query_string(query_string):
    # "a=1&b=hello%20world" -> {'a': '1', 'b': 'hello world'}
    params = {}
    if query_string == '':
        return params

    for pair in query_string.split('&'):
        if pair == '':
            continue
        if '=' in pair:
            key, value = pair.split('=', 1)
        else:
            key = pair
            value = ''
        key = percent_decode(key)
        value = percent_decode(value)
        # if a key shows up twice we just keep the first one
        if key not in params:
            params[key] = value
    return params


HEX_DIGITS = '0123456789abcdefABCDEF'


def percent_decode(s):
    # undo url encoding. "%41" -> "A", "+" -> " "
    # we build a list of bytes first so multi byte utf-8 chars work
    out = bytearray()
    i = 0
    while i < len(s):
        c = s[i]
        if c == '%':
            # need 2 hex digits after the %
            if i + 2 >= len(s):
                raise BadRequest(400, 'bad percent encoding')
            h1 = s[i + 1]
            h2 = s[i + 2]
            if h1 not in HEX_DIGITS or h2 not in HEX_DIGITS:
                raise BadRequest(400, 'bad percent encoding')
            out.append(int(h1 + h2, 16))
            i = i + 3
        elif c == '+':
            out.append(ord(' '))
            i = i + 1
        else:
            out.append(ord(c))
            i = i + 1

    try:
        return out.decode('utf-8')
    except UnicodeDecodeError:
        raise BadRequest(400, 'query is not valid utf-8')
