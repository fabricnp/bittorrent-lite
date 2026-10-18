"""
choking.py - Manages which peers we unchoke.

Basic approach:
  - Keep track of how much each peer has uploaded to us
  - Every 10 seconds, unchoke the top N peers by upload rate
  - Every 30 seconds, optimistically unchoke one random peer
  - Peers that never upload (free-riders) get choked
"""

import time
import random
import logging

logger = logging.getLogger('choking')


class ChokingManager:
    """
    Decides which peers to unchoke based on their contribution.
    """

    def __init__(self, max_unchoked=4, eval_interval=10, optimistic_interval=30):
        self.max_unchoked = max_unchoked
        self.eval_interval = eval_interval
        self.optimistic_interval = optimistic_interval

        # tracking
        self.peer_upload_rates = {}   # peer_id -> bytes uploaded to us recently
        self.unchoked_peers = set()
        self.optimistic_peer = None

        self.last_eval_time = 0
        self.last_optimistic_time = 0

    def register_peer(self, peer_id):
        """Register a new peer."""
        self.peer_upload_rates[peer_id] = 0

    def unregister_peer(self, peer_id):
        """Remove a peer that disconnected."""
        self.peer_upload_rates.pop(peer_id, None)
        self.unchoked_peers.discard(peer_id)
        if self.optimistic_peer == peer_id:
            self.optimistic_peer = None

    def record_upload(self, peer_id, num_bytes):
        """Record that a peer uploaded data to us."""
        if peer_id in self.peer_upload_rates:
            self.peer_upload_rates[peer_id] += num_bytes

    def should_unchoke(self, peer_id):
        """Quick check: should we unchoke this peer right now?"""
        return peer_id in self.unchoked_peers or peer_id == self.optimistic_peer

    def evaluate(self, connected_peers):
        """Call this periodically. connected_peers is peer_id -> PeerConnection.
        Returns (to_unchoke, to_choke) sets of peer_ids to act on."""
        now = time.time()
        to_unchoke = set()
        to_choke = set()

        # regular evaluation
        if now - self.last_eval_time >= self.eval_interval:
            self.last_eval_time = now

            # sort interested peers by upload rate (how much they gave us)
            interested_peers = [
                pid for pid, conn in connected_peers.items()
                if conn.peer_interested
            ]

            # sort by contribution - most generous peers get unchoked
            interested_peers.sort(
                key=lambda pid: self.peer_upload_rates.get(pid, 0),
                reverse=True
            )

            # unchoke top N
            new_unchoked = set(interested_peers[:self.max_unchoked])

            # figure out who to choke/unchoke
            for pid in new_unchoked - self.unchoked_peers:
                to_unchoke.add(pid)
            for pid in self.unchoked_peers - new_unchoked:
                if pid != self.optimistic_peer:
                    to_choke.add(pid)

            self.unchoked_peers = new_unchoked

            # log free-riders
            for pid in interested_peers:
                if self.peer_upload_rates.get(pid, 0) == 0:
                    logger.info(f"Free-rider detected: {pid}")

            # reset upload counters for next interval
            for pid in self.peer_upload_rates:
                self.peer_upload_rates[pid] = 0

        # optimistic unchoking - give a random choked peer a chance
        if now - self.last_optimistic_time >= self.optimistic_interval:
            self.last_optimistic_time = now

            choked_interested = [
                pid for pid, conn in connected_peers.items()
                if conn.peer_interested and pid not in self.unchoked_peers
            ]

            if choked_interested:
                # choke old optimistic peer if they're not in the regular unchoked set
                if self.optimistic_peer and self.optimistic_peer not in self.unchoked_peers:
                    to_choke.add(self.optimistic_peer)

                self.optimistic_peer = random.choice(choked_interested)
                to_unchoke.add(self.optimistic_peer)
                logger.info(f"Optimistic unchoke: {self.optimistic_peer}")

        return to_unchoke, to_choke

    def get_stats(self):
        """Return current choking state for logging."""
        return {
            'unchoked': list(self.unchoked_peers),
            'optimistic': self.optimistic_peer,
            'upload_rates': dict(self.peer_upload_rates),
        }
