"""
piece_selector.py - Different strategies for choosing which piece to download next.

Three strategies:
  1. RarestFirst  - pick the piece that fewest peers have
  2. RandomSelect - pick a random available piece
  3. SequentialSelect - pick pieces in order (0, 1, 2, ...)
"""

import random
from collections import Counter


class RarestFirst:
    """
    Pick the piece that the fewest peers in the swarm have.
    Ties broken randomly. This helps distribute rare pieces faster
    and improves overall swarm health.
    """

    def __init__(self):
        # track how many peers have each piece
        # updated externally when we get bitfields and HAVEs
        self.piece_availability = Counter()

    def update_availability(self, bitfield):
        """Update availability counts from a peer's bitfield."""
        for i, has in enumerate(bitfield):
            if has:
                self.piece_availability[i] += 1

    def remove_peer_availability(self, bitfield):
        """Remove a peer's contribution when they disconnect."""
        for i, has in enumerate(bitfield):
            if has:
                self.piece_availability[i] = max(0, self.piece_availability[i] - 1)

    def select(self, missing_pieces, peer_bitfield, pending_requests):
        """Rarest piece the peer has that we haven't already asked for. None if nothing fits."""
        candidates = []
        for idx in missing_pieces:
            if idx in pending_requests:
                continue
            if idx < len(peer_bitfield) and peer_bitfield[idx]:
                candidates.append(idx)

        if not candidates:
            return None

        # sort by availability (rarest first)
        # for ties, shuffle first so we get random tie-breaking
        random.shuffle(candidates)
        candidates.sort(key=lambda idx: self.piece_availability.get(idx, 0))

        return candidates[0]


class RandomSelect:
    """Pick a random available piece from the peer."""

    def select(self, missing_pieces, peer_bitfield, pending_requests):
        candidates = []
        for idx in missing_pieces:
            if idx in pending_requests:
                continue
            if idx < len(peer_bitfield) and peer_bitfield[idx]:
                candidates.append(idx)

        if not candidates:
            return None

        return random.choice(candidates)

    def update_availability(self, bitfield):
        pass  # not needed for random

    def remove_peer_availability(self, bitfield):
        pass


class SequentialSelect:
    """Pick pieces in sequential order (lowest index first)."""

    def select(self, missing_pieces, peer_bitfield, pending_requests):
        for idx in sorted(missing_pieces):
            if idx in pending_requests:
                continue
            if idx < len(peer_bitfield) and peer_bitfield[idx]:
                return idx
        return None

    def update_availability(self, bitfield):
        pass

    def remove_peer_availability(self, bitfield):
        pass
