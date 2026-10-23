import argparse
import os
import sys
import threading

from ..metainfo.metainfo import load_metainfo
from ..peer.pieces import assemble_pieces
from ..tracker.client import TrackerClient, TrackerError
from .peer_manager import DEFAULT_MAX_PEERS, DEFAULT_PIPELINE_SIZE, PeerManager
from .peer_server import PeerServer


def run_seeder(args):
    metainfo = load_metainfo(args.metainfo)

    if not os.path.isfile(args.file):
        sys.exit(f"file not found: {args.file}")

    if os.path.getsize(args.file) != metainfo.file_size:
        sys.exit("--file size does not match the metainfo")

    server = PeerServer(args.peer_id, args.swarm_id, metainfo, args.file)
    port = server.start(args.host, args.port)

    tracker = TrackerClient(args.tracker_host, args.tracker_port)
    tracker.register(args.swarm_id, args.peer_id, args.host, port)

    print(f"[SEEDER] {args.peer_id} serving on {args.host}:{port}")

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\n[SEEDER] Shutting down")
    finally:
        tracker.unregister(args.swarm_id, args.peer_id)
        server.stop()


def run_leecher(args):
    metainfo = load_metainfo(args.metainfo)

    tracker = TrackerClient(args.tracker_host, args.tracker_port)
    peers = tracker.get_peers(args.swarm_id, exclude=args.peer_id)

    print(f"[LEECHER] Tracker returned {len(peers)} peer(s)")

    manager = PeerManager(
        args.peer_id,
        args.swarm_id,
        metainfo,
        args.pieces_dir,
        pipeline_size=args.pipeline,
        max_peers=args.max_peers,
    )

    connected = manager.connect_to_peers(peers)

    print(f"[LEECHER] Connected to {connected} peer(s)")

    manager.start()
    complete = manager.wait_until_complete(args.timeout)
    manager.close()

    if not complete:
        sys.exit("download incomplete")

    assemble_pieces(args.pieces_dir, args.output, metainfo.piece_count)

    print(f"[LEECHER] Download complete: {args.output}")


def main():
    parser = argparse.ArgumentParser(description="P2P swarm node")
    subparsers = parser.add_subparsers(dest="role", required=True)

    def add_common(sub):
        sub.add_argument("--peer-id", required=True)
        sub.add_argument("--swarm-id", required=True)
        sub.add_argument("--metainfo", required=True)
        sub.add_argument("--tracker-host", default="127.0.0.1")
        sub.add_argument("--tracker-port", type=int, default=7000)

    seed = subparsers.add_parser("seed", help="serve a complete file")
    add_common(seed)
    seed.add_argument("--file", required=True)
    seed.add_argument("--host", default="127.0.0.1")
    seed.add_argument("--port", type=int, default=0)
    seed.set_defaults(handler=run_seeder)

    leech = subparsers.add_parser("leech", help="download a file from the swarm")
    add_common(leech)
    leech.add_argument("--pieces-dir", default="pieces")
    leech.add_argument("--output", required=True)
    leech.add_argument("--pipeline", type=int, default=DEFAULT_PIPELINE_SIZE)
    leech.add_argument("--max-peers", type=int, default=DEFAULT_MAX_PEERS)
    leech.add_argument("--timeout", type=float, default=60.0)
    leech.set_defaults(handler=run_leecher)

    args = parser.parse_args()

    try:
        args.handler(args)
    except TrackerError as exc:
        sys.exit(f"tracker error: {exc}")


if __name__ == "__main__":
    main()
