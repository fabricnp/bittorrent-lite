# tracker.py
# Member B (Pravesh) - the tracker server.
#
# Its a tiny HTTP server written on top of asyncio TCP sockets (no http
# library). Peers send GET /announce with their info_hash, peer_id and
# port, we remember them in a Swarm and send back a list of the other
# peers as JSON. Endpoints:
#
#   GET /announce?info_hash=..&peer_id=..&port=..[&event=..&left=..&numwant=..]
#   GET /scrape?info_hash=..      -> seeders / leechers count for one swarm
#   GET /stats                    -> everything the tracker knows (debugging)
#
# How to run it:
#   python -m src.tracker.tracker
#   python -m src.tracker.tracker --host 0.0.0.0 --port 8000 --interval 30

import argparse
import asyncio
import json

from .http_request import read_request_head, parse_request, BadRequest
from .swarm import Swarm


STATUS_TEXT = {
    200: 'OK',
    400: 'Bad Request',
    404: 'Not Found',
    405: 'Method Not Allowed',
    414: 'URI Too Long',
    431: 'Request Header Fields Too Large',
    500: 'Internal Server Error',
    505: 'HTTP Version Not Supported',
}

VALID_EVENTS = ['', 'started', 'completed', 'stopped']
DEFAULT_NUMWANT = 50
MAX_NUMWANT = 200


class TrackerServer:

    def __init__(self, host='127.0.0.1', port=8000, interval=30, peer_timeout=120,
                 cleanup_every=30, verbose=True):
        self.host = host
        self.port = port
        # how often (seconds) we tell peers to announce again
        self.interval = interval
        # if a peer is quiet for this long we treat it as dead
        self.peer_timeout = peer_timeout
        # how often the background cleanup runs
        self.cleanup_every = cleanup_every
        self.verbose = verbose

        self.swarms = {}   # info_hash (hex string) -> Swarm
        self.server = None
        self.cleanup_task = None

        # small counters, shown in /stats
        self.total_announces = 0
        self.total_bad_requests = 0

    def log(self, msg):
        if self.verbose:
            print('[tracker] ' + msg)

    async def start(self):
        self.server = await asyncio.start_server(self.handle_client, self.host, self.port)
        self.cleanup_task = asyncio.create_task(self.cleanup_loop())
        self.log('Listening on %s:%d' % (self.host, self.port))

    async def stop(self):
        if self.cleanup_task is not None:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
            self.cleanup_task = None

        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
            self.log('Stopped')

    # ---------- connection handling ----------

    async def handle_client(self, reader, writer):
        addr = writer.get_extra_info('peername')
        if addr:
            client_ip = addr[0]
        else:
            client_ip = '127.0.0.1'

        try:
            try:
                head = await read_request_head(reader)
                if head is None:
                    # they connected and left without sending anything
                    return
                request = parse_request(head)
                status, body = self.route(request, client_ip)
            except BadRequest as e:
                self.total_bad_requests = self.total_bad_requests + 1
                status = e.status
                body = {'failure reason': e.message}
            except asyncio.TimeoutError:
                # too slow, just hang up
                return

            writer.write(self.build_response(status, body))
            await writer.drain()

        except Exception as e:
            # something we didnt expect, try to tell the client at least
            self.log('error with %s: %s' % (str(addr), str(e)))
            try:
                writer.write(self.build_response(500, {'failure reason': 'internal error'}))
                await writer.drain()
            except Exception:
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    def route(self, request, client_ip):
        # decide which handler gets the request
        if request.method != 'GET':
            return 405, {'failure reason': 'only GET is supported'}

        if request.path == '/announce':
            return self.handle_announce(request.query, client_ip)
        if request.path == '/scrape':
            return self.handle_scrape(request.query)
        if request.path == '/stats':
            return self.handle_stats()
        return 404, {'failure reason': 'unknown path ' + request.path}

    def build_response(self, status, body):
        body_bytes = json.dumps(body).encode('utf-8')
        text = STATUS_TEXT.get(status, 'Error')

        head = 'HTTP/1.1 ' + str(status) + ' ' + text + '\r\n'
        head = head + 'Content-Type: application/json\r\n'
        head = head + 'Content-Length: ' + str(len(body_bytes)) + '\r\n'
        if status == 405:
            head = head + 'Allow: GET\r\n'
        head = head + 'Connection: close\r\n'
        head = head + '\r\n'
        return head.encode('ascii') + body_bytes

    # ---------- endpoints ----------

    def handle_announce(self, params, client_ip):
        # check all the params first, any problem -> 400
        info_hash = check_info_hash(params.get('info_hash'))

        peer_id = params.get('peer_id')
        if peer_id is None or peer_id == '':
            raise BadRequest(400, 'missing peer_id')
        if len(peer_id) > 64:
            raise BadRequest(400, 'peer_id too long')

        port = read_int(params, 'port', None)
        if port is None:
            raise BadRequest(400, 'missing port')
        if port < 1 or port > 65535:
            raise BadRequest(400, 'port must be between 1 and 65535')

        event = params.get('event', '')
        if event not in VALID_EVENTS:
            raise BadRequest(400, 'unknown event ' + event)

        uploaded = read_int(params, 'uploaded', 0)
        downloaded = read_int(params, 'downloaded', 0)
        left = read_int(params, 'left', None)

        numwant = read_int(params, 'numwant', DEFAULT_NUMWANT)
        if numwant > MAX_NUMWANT:
            numwant = MAX_NUMWANT

        self.total_announces = self.total_announces + 1

        # get the swarm, make it if its the first peer for this torrent.
        # (no point making a swarm just for someone who is leaving)
        swarm = self.swarms.get(info_hash)
        if swarm is None:
            if event == 'stopped':
                return 200, self.announce_reply(None, [])
            swarm = Swarm(info_hash)
            self.swarms[info_hash] = swarm

        was_seeder = peer_id in swarm.peers and swarm.peers[peer_id].is_seeder
        still_in = swarm.announce(peer_id, client_ip, port, event,
                                  uploaded, downloaded, left)

        short = info_hash[:12]
        if not still_in:
            self.log('Peer %s left swarm %s...' % (peer_id, short))
            if swarm.is_empty():
                del self.swarms[info_hash]
            return 200, self.announce_reply(swarm, [])

        if event == 'started':
            self.log('Peer %s joined swarm %s...' % (peer_id, short))
        elif event == 'completed' or (swarm.peers[peer_id].is_seeder and not was_seeder):
            self.log('Peer %s is now seeding in swarm %s...' % (peer_id, short))

        peer_list = swarm.get_peer_list(exclude_peer_id=peer_id, numwant=numwant)
        return 200, self.announce_reply(swarm, peer_list)

    def announce_reply(self, swarm, peer_list):
        seeders = 0
        leechers = 0
        if swarm is not None:
            seeders = swarm.count_seeders()
            leechers = swarm.count_leechers()
        return {
            'interval': self.interval,
            'min interval': self.interval // 2,
            'complete': seeders,      # number of seeders
            'incomplete': leechers,   # number of leechers
            'peers': peer_list,
        }

    def handle_scrape(self, params):
        info_hash = check_info_hash(params.get('info_hash'))
        swarm = self.swarms.get(info_hash)
        if swarm is None:
            return 200, {'info_hash': info_hash, 'complete': 0, 'incomplete': 0,
                         'downloaded': 0, 'peers': 0}

        # clean first, so the numbers we report are up to date
        swarm.remove_dead_peers(self.peer_timeout)
        return 200, {
            'info_hash': info_hash,
            'complete': swarm.count_seeders(),
            'incomplete': swarm.count_leechers(),
            'downloaded': swarm.times_completed,
            'peers': len(swarm.peers),
        }

    def handle_stats(self):
        swarms_info = {}
        for ih in self.swarms:
            swarm = self.swarms[ih]
            swarms_info[ih] = {
                'peers': len(swarm.peers),
                'seeders': swarm.count_seeders(),
                'leechers': swarm.count_leechers(),
                'peer_ids': list(swarm.peers.keys()),
            }
        return 200, {
            'swarms': swarms_info,
            'total_announces': self.total_announces,
            'bad_requests': self.total_bad_requests,
        }

    # ---------- cleanup ----------

    def cleanup_now(self, now=None):
        # remove dead peers from every swarm, and remove swarms that are empty
        empty = []
        for ih in self.swarms:
            dead = self.swarms[ih].remove_dead_peers(self.peer_timeout, now)
            for pid in dead:
                self.log('Timed out peer ' + pid)
            if self.swarms[ih].is_empty():
                empty.append(ih)
        for ih in empty:
            del self.swarms[ih]

    async def cleanup_loop(self):
        while True:
            await asyncio.sleep(self.cleanup_every)
            self.cleanup_now()


# ---------- small helpers for checking params ----------

def check_info_hash(value):
    # our info_hash is SHA-256 so in hex its 64 characters
    if value is None or value == '':
        raise BadRequest(400, 'missing info_hash')
    if len(value) != 64:
        raise BadRequest(400, 'info_hash must be 64 hex characters')
    for ch in value:
        if ch not in '0123456789abcdefABCDEF':
            raise BadRequest(400, 'info_hash must be hex')
    return value.lower()


def read_int(params, name, default):
    # read a number param, it must be a whole number >= 0
    if name not in params:
        return default
    value = params[name]
    if not value.isdigit():
        raise BadRequest(400, name + ' must be a non-negative integer')
    return int(value)


async def run_tracker(host='127.0.0.1', port=8000, interval=30, peer_timeout=120):
    tracker = TrackerServer(host, port, interval=interval, peer_timeout=peer_timeout)
    await tracker.start()
    try:
        # just wait forever, Ctrl+C stops it
        await asyncio.Event().wait()
    finally:
        await tracker.stop()


def main():
    parser = argparse.ArgumentParser(description='BitTorrent-lite tracker')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--interval', type=int, default=30,
                        help='seconds between re-announces we ask peers for')
    parser.add_argument('--timeout', type=int, default=120,
                        help='drop a peer after this many quiet seconds')
    args = parser.parse_args()

    try:
        asyncio.run(run_tracker(args.host, args.port, args.interval, args.timeout))
    except KeyboardInterrupt:
        print('[tracker] bye')


if __name__ == '__main__':
    main()
