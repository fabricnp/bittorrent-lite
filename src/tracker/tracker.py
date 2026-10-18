"""
tracker.py - Simple HTTP tracker built on raw TCP sockets.

Handles peer registration and announce requests.
Peers hit /announce with their info_hash, peer_id, port, etc.
Tracker responds with a list of peers in the swarm.

No external HTTP libraries - we parse HTTP manually.
"""

import asyncio
import json
import time
import urllib.parse


class TrackerServer:
    """
    Tracker that maintains swarm state and responds to announces.
    Runs as an asyncio TCP server.
    """

    def __init__(self, host='127.0.0.1', port=8000):
        self.host = host
        self.port = port
        # swarm state: info_hash -> {peer_id: {ip, port, last_seen, uploaded, downloaded}}
        self.swarms = {}
        self.server = None
        self.peer_timeout = 120  # seconds before a peer is considered dead

    async def start(self):
        """Start the tracker server."""
        self.server = await asyncio.start_server(
            self._handle_connection, self.host, self.port
        )
        print(f"[tracker] Listening on {self.host}:{self.port}")

    async def stop(self):
        """Shut down the tracker."""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            print("[tracker] Stopped")

    async def _handle_connection(self, reader, writer):
        """Handle an incoming TCP connection."""
        addr = writer.get_extra_info('peername')
        try:
            # read the HTTP request (we only expect small requests)
            raw = await asyncio.wait_for(reader.read(4096), timeout=10)
            if not raw:
                return

            request_text = raw.decode('utf-8', errors='replace')
            response = self._process_request(request_text, addr)
            writer.write(response)
            await writer.drain()

        except asyncio.TimeoutError:
            pass
        except Exception as e:
            print(f"[tracker] Error from {addr}: {e}")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    def _process_request(self, request_text, addr):
        """Parse HTTP request and route to handler."""
        lines = request_text.split('\r\n')
        if not lines:
            return self._http_response(400, {'error': 'empty request'})

        # parse request line: GET /announce?params HTTP/1.1
        parts = lines[0].split(' ')
        if len(parts) < 2:
            return self._http_response(400, {'error': 'malformed request'})

        method = parts[0]
        path = parts[1]

        if method != 'GET':
            return self._http_response(405, {'error': 'only GET supported'})

        if path.startswith('/announce'):
            return self._handle_announce(path, addr)
        elif path.startswith('/scrape'):
            return self._handle_scrape(path)
        elif path == '/stats':
            return self._handle_stats()
        else:
            return self._http_response(404, {'error': 'not found'})

    def _handle_announce(self, path, addr):
        """
        Handle /announce requests.

        Expected params:
            info_hash - hex string identifying the torrent
            peer_id   - unique id of the peer
            port      - port the peer is listening on
            event     - started/stopped/completed (optional)
            uploaded  - bytes uploaded (optional)
            downloaded - bytes downloaded (optional)
        """
        params = self._parse_query(path)

        info_hash = params.get('info_hash')
        peer_id = params.get('peer_id')
        port = params.get('port')

        if not all([info_hash, peer_id, port]):
            return self._http_response(400, {
                'error': 'missing required params (info_hash, peer_id, port)'
            })

        try:
            port = int(port)
        except ValueError:
            return self._http_response(400, {'error': 'port must be integer'})

        event = params.get('event', '')
        peer_ip = addr[0] if addr else '127.0.0.1'

        # init swarm if needed
        if info_hash not in self.swarms:
            self.swarms[info_hash] = {}

        swarm = self.swarms[info_hash]

        if event == 'stopped':
            # peer is leaving
            swarm.pop(peer_id, None)
            print(f"[tracker] Peer {peer_id} left swarm {info_hash[:12]}...")
        else:
            # register or update peer
            swarm[peer_id] = {
                'ip': peer_ip,
                'port': port,
                'last_seen': time.time(),
                'uploaded': int(params.get('uploaded', 0)),
                'downloaded': int(params.get('downloaded', 0)),
            }
            if event == 'started':
                print(f"[tracker] Peer {peer_id} joined swarm {info_hash[:12]}...")
            elif event == 'completed':
                print(f"[tracker] Peer {peer_id} completed download in swarm {info_hash[:12]}...")

        # clean out dead peers
        self._cleanup_swarm(info_hash)

        # build peer list (exclude the requesting peer)
        peers = []
        for pid, pinfo in swarm.items():
            if pid != peer_id:
                peers.append({
                    'peer_id': pid,
                    'ip': pinfo['ip'],
                    'port': pinfo['port'],
                })

        response_data = {
            'interval': 30,  # re-announce interval in seconds
            'peers': peers,
            'complete': len(swarm),  # total peers in swarm
        }

        return self._http_response(200, response_data)

    def _handle_scrape(self, path):
        """Return basic stats about a swarm."""
        params = self._parse_query(path)
        info_hash = params.get('info_hash')

        if not info_hash:
            return self._http_response(400, {'error': 'need info_hash'})

        swarm = self.swarms.get(info_hash, {})
        self._cleanup_swarm(info_hash)

        return self._http_response(200, {
            'info_hash': info_hash,
            'peers': len(swarm),
        })

    def _handle_stats(self):
        """Return overall tracker stats."""
        stats = {}
        for ih, swarm in self.swarms.items():
            stats[ih] = {
                'peers': len(swarm),
                'peer_ids': list(swarm.keys()),
            }
        return self._http_response(200, {'swarms': stats})

    def _cleanup_swarm(self, info_hash):
        """Remove peers that haven't announced recently."""
        if info_hash not in self.swarms:
            return
        now = time.time()
        swarm = self.swarms[info_hash]
        dead = [pid for pid, p in swarm.items()
                if now - p['last_seen'] > self.peer_timeout]
        for pid in dead:
            del swarm[pid]
            print(f"[tracker] Timed out peer {pid}")

    def _parse_query(self, path):
        """Extract query parameters from URL path."""
        if '?' not in path:
            return {}
        query_string = path.split('?', 1)[1]
        return dict(urllib.parse.parse_qsl(query_string))

    def _http_response(self, status_code, body_dict):
        """Build a raw HTTP response with JSON body."""
        status_text = {200: 'OK', 400: 'Bad Request', 404: 'Not Found', 405: 'Method Not Allowed'}
        body = json.dumps(body_dict).encode('utf-8')

        resp = f"HTTP/1.1 {status_code} {status_text.get(status_code, 'Error')}\r\n"
        resp += f"Content-Type: application/json\r\n"
        resp += f"Content-Length: {len(body)}\r\n"
        resp += f"Connection: close\r\n"
        resp += f"\r\n"

        return resp.encode('utf-8') + body


async def run_tracker(host='127.0.0.1', port=8000):
    """Convenience function to run the tracker."""
    tracker = TrackerServer(host, port)
    await tracker.start()
    try:
        await asyncio.Event().wait()  # run forever
    except asyncio.CancelledError:
        pass
    finally:
        await tracker.stop()


if __name__ == '__main__':
    asyncio.run(run_tracker())
