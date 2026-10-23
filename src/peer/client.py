"""
client.py - The main peer client that orchestrates everything.

A peer client can be a seeder (has the complete file) or a leecher
(downloading). It connects to the tracker, gets peer lists, and
manages connections to multiple peers concurrently.
"""

import asyncio
import logging
import os
import time
import uuid
import json
import urllib.parse

from ..metainfo import metainfo, FileAssembler
from ..metainfo.file_splitter import get_piece_data, compute_file_hash
from .peer_connection import PeerConnection
from . import protocol

logger = logging.getLogger('client')


class PeerClient:
    """The thing a user actually runs - talks to the tracker, accepts/opens
    connections to other peers, and keeps the download (or upload, if we're
    seeding) moving."""

    def __init__(self, torrent_path, download_dir='.', listen_port=6881,
                 peer_id=None, piece_selector=None, choking_manager=None,
                 is_seeder=False, source_file=None, rate_limit=0):
        # is_seeder + source_file means we already have the whole file and
        # just load it straight in below instead of downloading anything
        # parse the torrent file
        self.torrent_info = metainfo.parse(torrent_path)
        self.info_hash = self.torrent_info['info_hash']
        self.info_hash_hex = self.torrent_info['info_hash_hex']
        self.tracker_url = self.torrent_info['tracker_url']

        self.download_dir = download_dir
        self.listen_port = listen_port
        self.peer_id = peer_id or f"BT-{uuid.uuid4().hex[:16]}"
        self.piece_selector = piece_selector
        self.choking_manager = choking_manager
        self.is_seeder = is_seeder
        self.source_file = source_file
        self.rate_limit = rate_limit

        # set up assembler
        self.assembler = FileAssembler(
            file_name=self.torrent_info['file_name'],
            file_length=self.torrent_info['file_length'],
            piece_length=self.torrent_info['piece_length'],
            piece_hashes=self.torrent_info['piece_hashes'],
            download_dir=download_dir,
        )

        # if seeder, mark all pieces as available
        if is_seeder and source_file:
            self._load_as_seeder()

        # active connections
        self.connections = {}  # remote_peer_id -> PeerConnection
        self.server = None

        # stats
        self.start_time = None
        self.end_time = None

    def _load_as_seeder(self):
        """Load all pieces from the source file (for seeders)."""
        for i in range(self.assembler.num_pieces):
            data = get_piece_data(
                self.source_file, i, self.torrent_info['piece_length']
            )
            self.assembler.add_piece(i, data)
        logger.info(f"Seeder loaded {self.assembler.num_pieces} pieces from {self.source_file}")

    def _provide_piece(self, piece_index):
        """Callback for providing piece data to requesting peers."""
        if piece_index in self.assembler.pieces_data:
            return self.assembler.pieces_data[piece_index]
        elif self.source_file and self.assembler.have[piece_index]:
            return get_piece_data(
                self.source_file, piece_index, self.torrent_info['piece_length']
            )
        return None

    def _on_piece_complete(self, piece_index):
        """Called when we finish downloading a piece. Broadcast HAVE to all peers."""
        # broadcast to all connected peers
        for conn in self.connections.values():
            if conn.connected:
                asyncio.create_task(conn.send_have(piece_index))

        progress = self.assembler.progress * 100
        logger.info(f"Progress: {progress:.1f}%")

        # check if download is complete
        if self.assembler.complete and self.end_time is None:
            self.end_time = time.time()
            elapsed = self.end_time - self.start_time
            logger.info(f"Download complete in {elapsed:.2f}s!")

    async def start(self):
        """Start the peer client."""
        self.start_time = time.time()
        os.makedirs(self.download_dir, exist_ok=True)

        # start listening for incoming connections
        self.server = await asyncio.start_server(
            self._handle_incoming, '0.0.0.0', self.listen_port
        )
        logger.info(f"Listening on port {self.listen_port}")

        # announce to tracker
        peers = await self._tracker_announce('started')

        # connect to peers from tracker
        connect_tasks = []
        for peer_info in peers:
            if peer_info['peer_id'] != self.peer_id:
                task = asyncio.create_task(
                    self._connect_to_peer(peer_info['ip'], peer_info['port'])
                )
                connect_tasks.append(task)

        if connect_tasks:
            await asyncio.gather(*connect_tasks, return_exceptions=True)

    async def run_until_complete(self, timeout=300):
        """
        Run until download completes or timeout.
        For seeders, runs until timeout (they just keep seeding).
        """
        start = time.time()

        while True:
            if not self.is_seeder and self.assembler.complete:
                break

            elapsed = time.time() - start
            if elapsed > timeout:
                logger.warning(f"Timeout after {timeout}s")
                break

            # periodically re-announce to tracker
            if int(elapsed) % 30 == 0 and int(elapsed) > 0:
                try:
                    new_peers = await self._tracker_announce()
                    for p in new_peers:
                        if p['peer_id'] not in self.connections and p['peer_id'] != self.peer_id:
                            asyncio.create_task(
                                self._connect_to_peer(p['ip'], p['port'])
                            )
                except Exception as e:
                    logger.debug(f"Re-announce failed: {e}")

            await asyncio.sleep(1)

        # if we finished downloading, assemble the file
        if self.assembler.complete and not self.is_seeder:
            output_path = self.assembler.assemble()
            # verify integrity
            if self.source_file:
                original_hash = compute_file_hash(self.source_file)
                self.assembler.verify_assembled_file(output_path, original_hash)

        return self.get_stats()

    async def stop(self):
        """Clean shutdown."""
        # tell tracker we're leaving
        try:
            await self._tracker_announce('stopped')
        except Exception:
            pass

        # close all peer connections
        for conn in list(self.connections.values()):
            await conn.disconnect()

        # stop server
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _handle_incoming(self, reader, writer):
        """Handle an incoming peer connection."""
        addr = writer.get_extra_info('peername')
        logger.info(f"Incoming connection from {addr}")

        conn = PeerConnection(
            peer_id=self.peer_id,
            info_hash=self.info_hash,
            assembler=self.assembler,
            piece_provider=self._provide_piece,
            on_piece_complete=self._on_piece_complete,
            piece_selector=self.piece_selector,
            choking_manager=self.choking_manager,
            rate_limit=self.rate_limit,
        )
        await conn.accept(reader, writer)

        # they connect to us, so we read their handshake first
        # then send ours
        try:
            remote_hash, remote_id = await asyncio.wait_for(
                protocol.read_handshake(reader), timeout=10
            )

            if remote_hash != self.info_hash:
                logger.warning("Incoming handshake: info_hash mismatch")
                await conn.disconnect()
                return

            conn.remote_peer_id = remote_id.decode('utf-8', errors='replace').strip('\x00')

            # send our handshake back
            hs = protocol.build_handshake(self.info_hash, self.peer_id)
            writer.write(hs)
            await writer.drain()

            # exchange bitfields
            await conn.send_bitfield()

            # unchoke them if we're a seeder (seeders are generous)
            if self.is_seeder:
                await conn.send_unchoke()

            self.connections[conn.remote_peer_id] = conn
            await conn.run()

        except Exception as e:
            logger.error(f"Incoming connection error: {e}")
        finally:
            if conn.remote_peer_id and conn.remote_peer_id in self.connections:
                del self.connections[conn.remote_peer_id]

    async def _connect_to_peer(self, ip, port):
        """Establish outgoing connection to a peer."""
        conn = PeerConnection(
            peer_id=self.peer_id,
            info_hash=self.info_hash,
            assembler=self.assembler,
            piece_provider=self._provide_piece,
            on_piece_complete=self._on_piece_complete,
            piece_selector=self.piece_selector,
            choking_manager=self.choking_manager,
            rate_limit=self.rate_limit,
        )

        if not await conn.connect(ip, port):
            return

        if not await conn.do_handshake():
            return

        # exchange bitfields
        await conn.send_bitfield()

        # unchoke if seeder
        if self.is_seeder:
            await conn.send_unchoke()

        self.connections[conn.remote_peer_id] = conn

        # run() loops until the connection closes, so it has to happen in the
        # background - if we awaited it here, start() would never return
        # (connect_to_peer is one of the tasks start() gathers on)
        asyncio.create_task(self._run_peer(conn))

    async def _run_peer(self, conn):
        try:
            await conn.run()
        finally:
            if conn.remote_peer_id in self.connections:
                del self.connections[conn.remote_peer_id]

    async def _tracker_announce(self, event=''):
        """Send announce request to tracker and get peer list."""
        params = {
            'info_hash': self.info_hash_hex,
            'peer_id': self.peer_id,
            'port': self.listen_port,
            'uploaded': sum(c.bytes_uploaded for c in self.connections.values()),
            'downloaded': sum(c.bytes_downloaded for c in self.connections.values()),
            # tracker uses left == 0 to count us as a seeder
            'left': self.assembler.bytes_left,
        }
        if event:
            params['event'] = event

        query = urllib.parse.urlencode(params)

        # parse tracker URL to get host and port
        # tracker_url looks like http://127.0.0.1:8000
        url = self.tracker_url
        if url.startswith('http://'):
            url = url[7:]
        host_port = url.split('/')[0]
        if ':' in host_port:
            t_host, t_port = host_port.split(':')
            t_port = int(t_port)
        else:
            t_host = host_port
            t_port = 80

        # build raw HTTP request
        request = f"GET /announce?{query} HTTP/1.1\r\n"
        request += f"Host: {t_host}:{t_port}\r\n"
        request += f"Connection: close\r\n"
        request += f"\r\n"

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(t_host, t_port), timeout=5
            )
            writer.write(request.encode())
            await writer.drain()

            response = await asyncio.wait_for(reader.read(8192), timeout=5)
            writer.close()
            await writer.wait_closed()

            # parse HTTP response - find the JSON body
            resp_text = response.decode('utf-8', errors='replace')
            body_start = resp_text.find('\r\n\r\n')
            if body_start >= 0:
                body = resp_text[body_start + 4:]
                data = json.loads(body)
                peers = data.get('peers', [])
                logger.info(f"Tracker returned {len(peers)} peers")
                return peers
            else:
                logger.warning("Couldn't parse tracker response")
                return []

        except Exception as e:
            logger.warning(f"Tracker announce failed: {e}")
            return []

    def get_stats(self):
        """Return download/upload statistics."""
        elapsed = 0
        if self.start_time:
            end = self.end_time or time.time()
            elapsed = end - self.start_time

        total_down = sum(c.bytes_downloaded for c in self.connections.values())
        total_up = sum(c.bytes_uploaded for c in self.connections.values())

        return {
            'peer_id': self.peer_id,
            'elapsed_seconds': round(elapsed, 3),
            'pieces_total': self.assembler.num_pieces,
            'pieces_downloaded': self.assembler.downloaded_pieces,
            'complete': self.assembler.complete,
            'progress': round(self.assembler.progress * 100, 1),
            'bytes_downloaded': total_down,
            'bytes_uploaded': total_up,
            'num_connections': len(self.connections),
        }
