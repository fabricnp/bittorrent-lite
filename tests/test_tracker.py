# test_tracker.py
# Member B (Pravesh) - tests for the tracker.
#
# run with:   python -m tests.test_tracker
#   (or)      python -m unittest tests.test_tracker -v
#
# Part 1 tests the small pieces (query parsing, request parsing, Swarm).
# Part 2 starts a real tracker on a free port and talks to it with raw
# sockets, same as a peer would.

import asyncio
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.tracker.http_request import (parse_request, parse_query_string,
                                      percent_decode, BadRequest)
from src.tracker.swarm import Swarm
from src.tracker.tracker import TrackerServer


HASH1 = 'ab' * 32   # fake info_hash, 64 hex chars
HASH2 = 'cd' * 32


# ================= part 1: small pieces =================

class TestQueryParsing(unittest.TestCase):

    def test_simple(self):
        q = parse_query_string('a=1&b=two')
        self.assertEqual(q, {'a': '1', 'b': 'two'})

    def test_empty(self):
        self.assertEqual(parse_query_string(''), {})

    def test_percent_and_plus(self):
        self.assertEqual(percent_decode('hello%20world'), 'hello world')
        self.assertEqual(percent_decode('a+b'), 'a b')
        self.assertEqual(percent_decode('%41%42c'), 'ABc')

    def test_bad_percent(self):
        with self.assertRaises(BadRequest):
            percent_decode('abc%2')
        with self.assertRaises(BadRequest):
            percent_decode('%zz')

    def test_key_without_value(self):
        self.assertEqual(parse_query_string('flag&x=1'), {'flag': '', 'x': '1'})


class TestRequestParsing(unittest.TestCase):

    def test_good_request(self):
        head = b'GET /announce?port=7000&peer_id=p1 HTTP/1.1\r\nHost: localhost\r\n'
        r = parse_request(head)
        self.assertEqual(r.method, 'GET')
        self.assertEqual(r.path, '/announce')
        self.assertEqual(r.query['port'], '7000')
        self.assertEqual(r.headers['host'], 'localhost')

    def test_bad_request_line(self):
        with self.assertRaises(BadRequest) as ctx:
            parse_request(b'GET /announce\r\n')
        self.assertEqual(ctx.exception.status, 400)

    def test_wrong_version(self):
        with self.assertRaises(BadRequest) as ctx:
            parse_request(b'GET / HTTP/2.0\r\n')
        self.assertEqual(ctx.exception.status, 505)

    def test_too_long(self):
        long_path = '/announce?x=' + 'a' * 3000
        with self.assertRaises(BadRequest) as ctx:
            parse_request(('GET ' + long_path + ' HTTP/1.1\r\n').encode())
        self.assertEqual(ctx.exception.status, 414)

    def test_bad_header(self):
        with self.assertRaises(BadRequest):
            parse_request(b'GET / HTTP/1.1\r\nthis is not a header\r\n')


class TestSwarm(unittest.TestCase):

    def test_add_and_list(self):
        s = Swarm(HASH1)
        s.announce('p1', '127.0.0.1', 7001, 'started', now=100)
        s.announce('p2', '127.0.0.1', 7002, 'started', now=100)
        peers = s.get_peer_list(exclude_peer_id='p1')
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0]['peer_id'], 'p2')
        self.assertEqual(peers[0]['port'], 7002)

    def test_update_port(self):
        s = Swarm(HASH1)
        s.announce('p1', '127.0.0.1', 7001, 'started', now=100)
        s.announce('p1', '127.0.0.1', 7999, '', now=110)
        self.assertEqual(len(s.peers), 1)
        self.assertEqual(s.peers['p1'].port, 7999)

    def test_stopped_removes(self):
        s = Swarm(HASH1)
        s.announce('p1', '127.0.0.1', 7001, 'started')
        still_there = s.announce('p1', '127.0.0.1', 7001, 'stopped')
        self.assertFalse(still_there)
        self.assertTrue(s.is_empty())

    def test_seeders_and_leechers(self):
        s = Swarm(HASH1)
        s.announce('seed', '127.0.0.1', 7001, 'started', left=0)
        s.announce('l1', '127.0.0.1', 7002, 'started', left=5000)
        s.announce('l2', '127.0.0.1', 7003, 'started')   # didnt send left
        self.assertEqual(s.count_seeders(), 1)
        self.assertEqual(s.count_leechers(), 2)

        # l1 finishes
        s.announce('l1', '127.0.0.1', 7002, 'completed', left=0)
        self.assertEqual(s.count_seeders(), 2)
        self.assertEqual(s.times_completed, 1)

    def test_numwant(self):
        s = Swarm(HASH1)
        for i in range(30):
            s.announce('p%d' % i, '127.0.0.1', 7000 + i, 'started')
        peers = s.get_peer_list(exclude_peer_id='p0', numwant=10)
        self.assertEqual(len(peers), 10)
        ids = [p['peer_id'] for p in peers]
        self.assertNotIn('p0', ids)
        self.assertEqual(len(set(ids)), 10)   # no duplicates

    def test_dead_peers(self):
        s = Swarm(HASH1)
        s.announce('old', '127.0.0.1', 7001, 'started', now=0)
        s.announce('new', '127.0.0.1', 7002, 'started', now=100)
        removed = s.remove_dead_peers(timeout=60, now=120)
        self.assertEqual(removed, ['old'])
        self.assertEqual(list(s.peers.keys()), ['new'])


# ================= part 2: real server over TCP =================

async def send_raw(port, raw_bytes, split_at=None):
    # send bytes to the tracker and return (status_code, json_body).
    # split_at lets us send the request in 2 pieces to check that the
    # tracker waits for the full request
    reader, writer = await asyncio.open_connection('127.0.0.1', port)
    if split_at is None:
        writer.write(raw_bytes)
    else:
        writer.write(raw_bytes[:split_at])
        await writer.drain()
        await asyncio.sleep(0.05)
        writer.write(raw_bytes[split_at:])
    await writer.drain()

    response = b''
    while True:
        chunk = await reader.read(4096)
        if not chunk:
            break
        response = response + chunk
    writer.close()
    await writer.wait_closed()

    head, body = response.split(b'\r\n\r\n', 1)
    status_line = head.split(b'\r\n')[0].decode()
    status = int(status_line.split(' ')[1])

    # check content-length is right
    for line in head.split(b'\r\n')[1:]:
        if line.lower().startswith(b'content-length:'):
            assert int(line.split(b':')[1]) == len(body)

    return status, json.loads(body.decode())


async def get(port, path, split_at=None):
    req = 'GET ' + path + ' HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n'
    return await send_raw(port, req.encode(), split_at)


def announce_path(info_hash, peer_id, port, event='', left=None, numwant=None):
    path = '/announce?info_hash=' + info_hash + '&peer_id=' + peer_id + '&port=' + str(port)
    if event:
        path = path + '&event=' + event
    if left is not None:
        path = path + '&left=' + str(left)
    if numwant is not None:
        path = path + '&numwant=' + str(numwant)
    return path


class TestTrackerServer(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        # port 0 = let the OS pick a free port
        self.tracker = TrackerServer('127.0.0.1', 0, verbose=False)
        await self.tracker.start()
        self.port = self.tracker.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        await self.tracker.stop()

    async def test_first_announce(self):
        status, body = await get(self.port, announce_path(HASH1, 'seed', 7001, 'started', left=0))
        self.assertEqual(status, 200)
        self.assertEqual(body['peers'], [])
        self.assertEqual(body['complete'], 1)
        self.assertEqual(body['incomplete'], 0)
        self.assertEqual(body['interval'], 30)

    async def test_second_peer_sees_first(self):
        await get(self.port, announce_path(HASH1, 'seed', 7001, 'started', left=0))
        status, body = await get(self.port, announce_path(HASH1, 'leech', 7002, 'started', left=100))
        self.assertEqual(status, 200)
        self.assertEqual(len(body['peers']), 1)
        p = body['peers'][0]
        self.assertEqual(p['peer_id'], 'seed')
        self.assertEqual(p['ip'], '127.0.0.1')
        self.assertEqual(p['port'], 7001)

    async def test_swarms_are_separate(self):
        await get(self.port, announce_path(HASH1, 'a', 7001, 'started'))
        status, body = await get(self.port, announce_path(HASH2, 'b', 7002, 'started'))
        self.assertEqual(body['peers'], [])
        self.assertEqual(len(self.tracker.swarms), 2)

    async def test_request_split_in_two(self):
        # TCP can deliver the request in pieces, tracker must still work
        await get(self.port, announce_path(HASH1, 'a', 7001, 'started'))
        status, body = await get(self.port, announce_path(HASH1, 'b', 7002, 'started'), split_at=20)
        self.assertEqual(status, 200)
        self.assertEqual(len(body['peers']), 1)

    async def test_stopped(self):
        await get(self.port, announce_path(HASH1, 'a', 7001, 'started'))
        await get(self.port, announce_path(HASH1, 'b', 7002, 'started'))
        await get(self.port, announce_path(HASH1, 'a', 7001, 'stopped'))
        status, body = await get(self.port, announce_path(HASH1, 'b', 7002))
        self.assertEqual(body['peers'], [])

        # last one leaves -> swarm is deleted
        await get(self.port, announce_path(HASH1, 'b', 7002, 'stopped'))
        self.assertEqual(self.tracker.swarms, {})

    async def test_stopped_unknown_swarm(self):
        status, body = await get(self.port, announce_path(HASH1, 'ghost', 7001, 'stopped'))
        self.assertEqual(status, 200)
        self.assertEqual(self.tracker.swarms, {})

    async def test_missing_params(self):
        status, body = await get(self.port, '/announce?peer_id=x&port=7000')
        self.assertEqual(status, 400)
        self.assertIn('info_hash', body['failure reason'])

        status, body = await get(self.port, '/announce?info_hash=' + HASH1 + '&port=7000')
        self.assertEqual(status, 400)

        status, body = await get(self.port, '/announce?info_hash=' + HASH1 + '&peer_id=x')
        self.assertEqual(status, 400)

    async def test_bad_values(self):
        bad_paths = [
            '/announce?info_hash=1234&peer_id=x&port=7000',            # short hash
            '/announce?info_hash=' + 'zz' * 32 + '&peer_id=x&port=7000', # not hex
            announce_path(HASH1, 'x', 0),                               # port 0
            announce_path(HASH1, 'x', 70000),                           # port too big
            '/announce?info_hash=' + HASH1 + '&peer_id=x&port=abc',
            announce_path(HASH1, 'x', 7000, event='paused'),            # unknown event
            announce_path(HASH1, 'x', 7000) + '&uploaded=-5',
        ]
        for path in bad_paths:
            status, body = await get(self.port, path)
            self.assertEqual(status, 400, path)
        # nothing should have been added
        self.assertEqual(self.tracker.swarms, {})

    async def test_not_found_and_method(self):
        status, body = await get(self.port, '/hello')
        self.assertEqual(status, 404)

        status, body = await send_raw(self.port, b'POST /announce HTTP/1.1\r\n\r\n')
        self.assertEqual(status, 405)

    async def test_garbage(self):
        status, body = await send_raw(self.port, b'hello there\r\n\r\n')
        self.assertEqual(status, 400)

    async def test_numwant(self):
        for i in range(10):
            await get(self.port, announce_path(HASH1, 'p%d' % i, 7000 + i, 'started'))
        status, body = await get(self.port, announce_path(HASH1, 'p0', 7000, numwant=3))
        self.assertEqual(len(body['peers']), 3)

    async def test_scrape_and_stats(self):
        await get(self.port, announce_path(HASH1, 'seed', 7001, 'started', left=0))
        await get(self.port, announce_path(HASH1, 'l1', 7002, 'started', left=10))
        await get(self.port, announce_path(HASH1, 'l1', 7002, 'completed', left=0))

        status, body = await get(self.port, '/scrape?info_hash=' + HASH1)
        self.assertEqual(status, 200)
        self.assertEqual(body['complete'], 2)
        self.assertEqual(body['incomplete'], 0)
        self.assertEqual(body['downloaded'], 1)

        status, body = await get(self.port, '/scrape?info_hash=' + HASH2)
        self.assertEqual(body['peers'], 0)

        status, body = await get(self.port, '/stats')
        self.assertEqual(status, 200)
        self.assertEqual(body['swarms'][HASH1]['peers'], 2)
        self.assertEqual(body['total_announces'], 3)

    async def test_cleanup_removes_dead_peers(self):
        await get(self.port, announce_path(HASH1, 'a', 7001, 'started'))
        last = self.tracker.swarms[HASH1].peers['a'].last_seen
        # pretend 200 seconds went by
        self.tracker.cleanup_now(now=last + 200)
        self.assertEqual(self.tracker.swarms, {})

    async def test_many_peers_at_once(self):
        # 8 peers announce at the same time (like the experiment does)
        tasks = []
        for i in range(8):
            tasks.append(get(self.port, announce_path(HASH1, 'peer%d' % i, 7100 + i, 'started')))
        await asyncio.gather(*tasks)
        self.assertEqual(len(self.tracker.swarms[HASH1].peers), 8)

        status, body = await get(self.port, announce_path(HASH1, 'peer0', 7100))
        self.assertEqual(len(body['peers']), 7)

    async def test_client_that_says_nothing(self):
        # connect and close without sending, tracker should not crash
        reader, writer = await asyncio.open_connection('127.0.0.1', self.port)
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.05)
        status, body = await get(self.port, '/stats')
        self.assertEqual(status, 200)


if __name__ == '__main__':
    unittest.main(verbosity=2)
