"""
peer_connection.py - Manages a single TCP connection to another peer.

Handles the state machine per connection:
  - handshake exchange
  - bitfield exchange
  - interested/choked state
  - sending requests and receiving pieces
  - sending pieces on request
"""

import asyncio
import time
import logging

from . import protocol

logger = logging.getLogger('peer')


class PeerConnection:
    """
    Represents one connection to a remote peer.
    Each peer has its own state: am_choking, am_interested, etc.
    """

    def __init__(self, peer_id, info_hash, assembler, piece_provider=None,
                 on_piece_complete=None, piece_selector=None, choking_manager=None,
                 rate_limit=0):
        # rate_limit: max bytes/sec we'll upload to this one peer, 0 = no limit
        self.our_peer_id = peer_id
        self.info_hash = info_hash
        self.assembler = assembler
        self.piece_provider = piece_provider
        self.on_piece_complete = on_piece_complete
        self.piece_selector = piece_selector
        self.choking_manager = choking_manager

        # connection state
        self.remote_peer_id = None
        self.reader = None
        self.writer = None
        self.connected = False

        # peer state (from our perspective)
        self.am_choking = True       # we are choking the remote peer
        self.am_interested = False   # we are interested in the remote peer
        self.peer_choking = True     # remote peer is choking us
        self.peer_interested = False # remote peer is interested in us

        # what the remote peer has
        self.peer_bitfield = None

        # request tracking
        self.pending_requests = set()  # piece indices we've requested
        self.max_pending = 5  # pipeline up to 5 requests

        # upload rate limiting (token bucket, bucket size = 1 second worth)
        self.rate_limit = rate_limit
        self._tokens = rate_limit
        self._last_refill = time.time()

        # stats
        self.bytes_downloaded = 0
        self.bytes_uploaded = 0
        self.pieces_downloaded = 0
        self.pieces_uploaded = 0
        self.connect_time = None
        self.last_activity = None

    async def connect(self, ip, port):
        """Initiate outgoing connection to a peer."""
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port), timeout=10
            )
            self.connected = True
            self.connect_time = time.time()
            self.last_activity = time.time()
            logger.info(f"Connected to {ip}:{port}")
            return True
        except (ConnectionRefusedError, asyncio.TimeoutError, OSError) as e:
            logger.warning(f"Failed to connect to {ip}:{port}: {e}")
            return False

    async def accept(self, reader, writer):
        """Set up from an incoming connection."""
        self.reader = reader
        self.writer = writer
        self.connected = True
        self.connect_time = time.time()
        self.last_activity = time.time()

    async def do_handshake(self):
        """Exchange handshakes with the peer."""
        if not self.connected:
            return False

        info_hash_bytes = self.info_hash
        if isinstance(info_hash_bytes, str):
            info_hash_bytes = bytes.fromhex(info_hash_bytes)

        # send our handshake
        hs = protocol.build_handshake(info_hash_bytes, self.our_peer_id)
        self.writer.write(hs)
        await self.writer.drain()

        # read their handshake
        try:
            remote_hash, remote_id = await asyncio.wait_for(
                protocol.read_handshake(self.reader), timeout=10
            )
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError) as e:
            logger.warning(f"Handshake failed: {e}")
            await self.disconnect()
            return False

        # verify info_hash matches
        if remote_hash != info_hash_bytes:
            logger.warning("Info hash mismatch during handshake")
            await self.disconnect()
            return False

        self.remote_peer_id = remote_id.decode('utf-8', errors='replace').strip('\x00')
        self.last_activity = time.time()
        logger.info(f"Handshake complete with peer {self.remote_peer_id}")
        return True

    async def send_bitfield(self):
        """Send our bitfield to the peer."""
        bf = self.assembler.get_bitfield()
        msg = protocol.build_bitfield(bf)
        self.writer.write(msg)
        await self.writer.drain()

    async def run(self):
        """
        Main message loop. Reads messages from the peer and handles them.
        Also sends requests for pieces we need.
        """
        try:
            while self.connected:
                try:
                    msg_id, payload = await asyncio.wait_for(
                        protocol.read_message(self.reader), timeout=120
                    )
                except asyncio.TimeoutError:
                    # send keepalive
                    self.writer.write(protocol.build_keepalive())
                    await self.writer.drain()
                    continue

                self.last_activity = time.time()

                if msg_id is None:
                    # keep-alive, nothing to do
                    continue

                await self._handle_message(msg_id, payload)

                # try to request more pieces if we can
                if not self.peer_choking and self.am_interested:
                    await self._request_pieces()

        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            logger.info(f"Connection lost with {self.remote_peer_id}")
        except Exception as e:
            logger.error(f"Error in peer loop ({self.remote_peer_id}): {e}")
        finally:
            await self.disconnect()

    async def _handle_message(self, msg_id, payload):
        """Dispatch incoming messages."""
        if msg_id == protocol.MSG_CHOKE:
            self.peer_choking = True
            self.pending_requests.clear()
            logger.debug(f"Peer {self.remote_peer_id} choked us")

        elif msg_id == protocol.MSG_UNCHOKE:
            self.peer_choking = False
            logger.debug(f"Peer {self.remote_peer_id} unchoked us")
            # start requesting pieces
            if self.am_interested:
                await self._request_pieces()

        elif msg_id == protocol.MSG_INTERESTED:
            self.peer_interested = True
            logger.debug(f"Peer {self.remote_peer_id} is interested")
            # check choking manager to decide if we should unchoke
            if self.choking_manager:
                should_unchoke = self.choking_manager.should_unchoke(self.remote_peer_id)
                if should_unchoke and self.am_choking:
                    await self.send_unchoke()

        elif msg_id == protocol.MSG_NOT_INTERESTED:
            self.peer_interested = False
            logger.debug(f"Peer {self.remote_peer_id} is not interested")

        elif msg_id == protocol.MSG_HAVE:
            piece_index = protocol.parse_have(payload)
            if self.peer_bitfield is not None and piece_index < len(self.peer_bitfield):
                self.peer_bitfield[piece_index] = True
            # check if we need this piece
            if not self.assembler.have[piece_index] and not self.am_interested:
                await self.send_interested()

        elif msg_id == protocol.MSG_BITFIELD:
            self.peer_bitfield = self.assembler.set_bitfield(payload)
            # figure out if they have anything we need
            for i, has in enumerate(self.peer_bitfield):
                if has and not self.assembler.have[i]:
                    await self.send_interested()
                    break

        elif msg_id == protocol.MSG_REQUEST:
            piece_index, begin, length = protocol.parse_request(payload)
            await self._handle_request(piece_index, begin, length)

        elif msg_id == protocol.MSG_PIECE:
            piece_index, begin, data = protocol.parse_piece(payload)
            await self._handle_piece(piece_index, begin, data)

        elif msg_id == protocol.MSG_CANCEL:
            # we just ignore cancels for now, not critical
            pass

    async def _throttle_upload(self, nbytes):
        """Block until we have enough tokens to send nbytes, if rate limited."""
        if self.rate_limit <= 0:
            return
        while True:
            now = time.time()
            self._tokens = min(self.rate_limit, self._tokens + (now - self._last_refill) * self.rate_limit)
            self._last_refill = now
            if self._tokens >= nbytes:
                self._tokens -= nbytes
                return
            # not enough yet, wait for roughly what's missing to refill
            missing = nbytes - self._tokens
            await asyncio.sleep(missing / self.rate_limit)

    async def _handle_request(self, piece_index, begin, length):
        """Handle an incoming piece request from the peer."""
        if self.am_choking:
            # we're choking them, ignore the request
            return

        if self.piece_provider is None:
            return

        try:
            data = self.piece_provider(piece_index)
            if data is None:
                return
            await self._throttle_upload(len(data))
            msg = protocol.build_piece(piece_index, begin, data)
            self.writer.write(msg)
            await self.writer.drain()

            self.bytes_uploaded += len(data)
            self.pieces_uploaded += 1
        except Exception as e:
            logger.error(f"Error serving piece {piece_index}: {e}")

    async def _handle_piece(self, piece_index, begin, data):
        """Handle received piece data."""
        self.pending_requests.discard(piece_index)
        self.bytes_downloaded += len(data)

        accepted = self.assembler.add_piece(piece_index, data)
        if accepted:
            self.pieces_downloaded += 1
            logger.info(
                f"Got piece {piece_index} from {self.remote_peer_id} "
                f"({self.assembler.downloaded_pieces}/{self.assembler.num_pieces})"
            )
            # notify others (broadcast HAVE)
            if self.on_piece_complete:
                self.on_piece_complete(piece_index)
        else:
            logger.warning(f"Piece {piece_index} rejected (bad hash)")

    async def _request_pieces(self):
        """Send requests for pieces we need, up to max_pending."""
        if self.peer_bitfield is None:
            return

        while len(self.pending_requests) < self.max_pending:
            # find a piece to request
            missing = self.assembler.missing_pieces()
            if not missing:
                # we have everything, lose interest
                if self.am_interested:
                    await self.send_not_interested()
                return

            # use piece selector if available, otherwise pick first available
            if self.piece_selector:
                piece_idx = self.piece_selector.select(
                    missing, self.peer_bitfield, self.pending_requests
                )
            else:
                # basic: pick first piece the peer has that we don't
                piece_idx = None
                for idx in missing:
                    if idx not in self.pending_requests and self.peer_bitfield[idx]:
                        piece_idx = idx
                        break

            if piece_idx is None:
                break  # nothing to request from this peer

            # figure out the piece length (last piece might be shorter)
            piece_len = self.assembler.piece_length
            if piece_idx == self.assembler.num_pieces - 1:
                # last piece
                remainder = self.assembler.file_length % piece_len
                if remainder != 0:
                    piece_len = remainder

            msg = protocol.build_request(piece_idx, 0, piece_len)
            self.writer.write(msg)
            await self.writer.drain()
            self.pending_requests.add(piece_idx)

    async def send_interested(self):
        if not self.am_interested:
            self.am_interested = True
            self.writer.write(protocol.build_interested())
            await self.writer.drain()

    async def send_not_interested(self):
        if self.am_interested:
            self.am_interested = False
            self.writer.write(protocol.build_not_interested())
            await self.writer.drain()

    async def send_unchoke(self):
        if self.am_choking:
            self.am_choking = False
            self.writer.write(protocol.build_unchoke())
            await self.writer.drain()
            logger.debug(f"Unchoked peer {self.remote_peer_id}")

    async def send_choke(self):
        if not self.am_choking:
            self.am_choking = True
            self.writer.write(protocol.build_choke())
            await self.writer.drain()
            logger.debug(f"Choked peer {self.remote_peer_id}")

    async def send_have(self, piece_index):
        """Announce to this peer that we got a new piece."""
        if self.connected and self.writer:
            msg = protocol.build_have(piece_index)
            self.writer.write(msg)
            await self.writer.drain()

    async def disconnect(self):
        """Close the connection."""
        self.connected = False
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
