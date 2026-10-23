# swarm.py
# Member B (Pravesh) - the swarm state the tracker keeps in memory.
#
# One Swarm object per torrent (per info_hash). Inside it we keep a dict
# of every peer that announced:
#
#   peers[peer_id] = PeerEntry(ip, port, uploaded, downloaded, left, last_seen, ...)
#
# The tracker doesnt know anything about pieces, it only remembers who
# is in the swarm, where to reach them, and if they are a seeder or not.

import random
import time


class PeerEntry:
    def __init__(self, peer_id, ip, port):
        self.peer_id = peer_id
        self.ip = ip
        self.port = port
        self.uploaded = 0
        self.downloaded = 0
        self.left = None        # bytes still needed, None if the peer didnt say
        self.is_seeder = False
        self.joined_at = time.time()
        self.last_seen = time.time()

    def to_dict(self):
        # this is what goes in the peer list we send back
        return {'peer_id': self.peer_id, 'ip': self.ip, 'port': self.port}


class Swarm:
    def __init__(self, info_hash):
        self.info_hash = info_hash
        self.peers = {}
        # how many peers ever sent event=completed (just a counter for stats)
        self.times_completed = 0

    def announce(self, peer_id, ip, port, event='', uploaded=0, downloaded=0, left=None, now=None):
        # add a new peer or update one we already know.
        # returns True if the peer is (still) in the swarm after this
        if now is None:
            now = time.time()

        if event == 'stopped':
            if peer_id in self.peers:
                del self.peers[peer_id]
            return False

        if peer_id in self.peers:
            peer = self.peers[peer_id]
            # peer might have restarted on a new port, so update it
            peer.ip = ip
            peer.port = port
        else:
            peer = PeerEntry(peer_id, ip, port)
            peer.joined_at = now
            self.peers[peer_id] = peer

        # remember this before we change anything (for the completed counter)
        was_seeder = peer.is_seeder

        peer.uploaded = uploaded
        peer.downloaded = downloaded
        peer.last_seen = now

        if left is not None:
            peer.left = left
            if left == 0:
                peer.is_seeder = True
            else:
                peer.is_seeder = False

        if event == 'completed':
            # once someone finishes they are a seeder
            if not was_seeder:
                self.times_completed = self.times_completed + 1
            peer.is_seeder = True
            peer.left = 0

        return True

    def get_peer_list(self, exclude_peer_id=None, numwant=50):
        # list of other peers for the one asking.
        # if there are more than numwant we pick a random bunch, so not
        # everyone gets the same first 50 peers (spreads connections out)
        others = []
        for pid in self.peers:
            if pid != exclude_peer_id:
                others.append(self.peers[pid])

        if len(others) > numwant:
            others = random.sample(others, numwant)

        result = []
        for p in others:
            result.append(p.to_dict())
        return result

    def count_seeders(self):
        count = 0
        for pid in self.peers:
            if self.peers[pid].is_seeder:
                count = count + 1
        return count

    def count_leechers(self):
        return len(self.peers) - self.count_seeders()

    def remove_dead_peers(self, timeout, now=None):
        # peers that didnt announce for `timeout` seconds probably crashed
        # or lost network, so we drop them. returns the ids we removed
        if now is None:
            now = time.time()

        dead = []
        for pid in self.peers:
            if now - self.peers[pid].last_seen > timeout:
                dead.append(pid)

        for pid in dead:
            del self.peers[pid]
        return dead

    def is_empty(self):
        return len(self.peers) == 0
