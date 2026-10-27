import threading


class PieceManager:
    """
    Piece bookkeeping for one download.

    Tracks which pieces we have, which pieces each connected peer has,
    and which pieces are in flight (requested, not yet received).
    A piece is in flight from at most one peer at a time.
    """

    def __init__(self, piece_count, have=None):
        if have is not None and len(have) != piece_count:
            raise ValueError("have must contain one flag per piece")

        self.piece_count = piece_count
        self._have = list(have) if have is not None else [False] * piece_count
        self._peer_pieces = {}
        self._in_flight = {}
        self._lock = threading.RLock()

    # ---- local pieces ----

    def local_bitfield(self):
        with self._lock:
            return list(self._have)

    def has_piece(self, index):
        with self._lock:
            return self._have[index]

    def is_complete(self):
        with self._lock:
            return all(self._have)

    def mark_downloaded(self, index):
        """Record a verified piece and clear its in-flight entry."""
        with self._lock:
            self._in_flight.pop(index, None)
            self._have[index] = True

    # ---- remote peers ----

    def add_peer(self, peer_id, bitfield):
        if len(bitfield) != self.piece_count:
            raise ValueError("bitfield must contain one flag per piece")

        with self._lock:
            self._peer_pieces[peer_id] = list(bitfield)

    def update_peer_have(self, peer_id, index):
        """Apply a HAVE message from a peer."""
        with self._lock:
            if peer_id not in self._peer_pieces:
                raise ValueError(f"unknown peer: {peer_id}")

            self._peer_pieces[peer_id][index] = True

    def remove_peer(self, peer_id):
        """Forget a peer. Returns the pieces that were in flight from it."""
        with self._lock:
            self._peer_pieces.pop(peer_id, None)

            released = sorted(
                index
                for index, owner in self._in_flight.items()
                if owner == peer_id
            )

            for index in released:
                del self._in_flight[index]

            return released

    def availability(self):
        """Number of connected peers that have each piece."""
        with self._lock:
            counts = [0] * self.piece_count

            for bitfield in self._peer_pieces.values():
                for index, flag in enumerate(bitfield):
                    if flag:
                        counts[index] += 1

            return counts

    # ---- in-flight requests ----

    def mark_in_flight(self, index, peer_id):
        with self._lock:
            if self._have[index]:
                raise ValueError(f"piece {index} is already downloaded")

            if index in self._in_flight:
                raise ValueError(f"piece {index} is already in flight")

            if not self._peer_pieces.get(peer_id, [False] * self.piece_count)[index]:
                raise ValueError(f"peer {peer_id} does not have piece {index}")

            self._in_flight[index] = peer_id

    def release_in_flight(self, index):
        with self._lock:
            self._in_flight.pop(index, None)

    def in_flight_owner(self, index):
        with self._lock:
            return self._in_flight.get(index)

    def in_flight_count(self, peer_id):
        with self._lock:
            return sum(
                1 for owner in self._in_flight.values() if owner == peer_id
            )

    # ---- scheduler input ----

    def requestable_pieces(self, peer_id):
        """Pieces this peer has that we neither have nor have requested."""
        with self._lock:
            bitfield = self._peer_pieces.get(peer_id)

            if bitfield is None:
                return []

            return [
                index
                for index in range(self.piece_count)
                if bitfield[index]
                and not self._have[index]
                and index not in self._in_flight
            ]
