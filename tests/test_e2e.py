"""
test_e2e.py - End-to-end integration test.

Spins up a tracker + 1 seeder + 2 leechers on localhost and verifies
that the file is correctly transferred and reassembled.
"""

import asyncio
import os
import sys
import tempfile
import random
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.metainfo.metainfo import generate, parse
from src.metainfo.file_splitter import compute_file_hash
from src.metainfo.assembler import FileAssembler
from src.tracker.tracker import TrackerServer
from src.peer.client import PeerClient
from src.strategy.piece_selector import RarestFirst
from src.strategy.choking import ChokingManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)


async def run_e2e_test():
    """Run a simple end-to-end test: 1 seeder -> 2 leechers."""
    print("=" * 60)
    print("E2E TEST: 1 seeder -> 2 leechers")
    print("=" * 60)

    # create temp directory
    tmpdir = tempfile.mkdtemp(prefix='bt_test_')
    print(f"Working dir: {tmpdir}")

    # create a test file (small, ~50KB for quick testing)
    test_file = os.path.join(tmpdir, 'testfile.bin')
    file_size = 50 * 1024  # 50 KB
    rng = random.Random(123)
    with open(test_file, 'wb') as f:
        f.write(bytes(rng.getrandbits(8) for _ in range(file_size)))

    original_hash = compute_file_hash(test_file)
    print(f"Original file hash: {original_hash.hex()[:16]}...")

    # generate torrent
    tracker_port = 9999
    torrent_path = os.path.join(tmpdir, 'testfile.torrent')
    generate(
        test_file,
        f'http://127.0.0.1:{tracker_port}',
        piece_length=4096,  # 4 KB pieces
        output_path=torrent_path,
    )

    # start tracker
    tracker = TrackerServer('127.0.0.1', tracker_port)
    await tracker.start()

    seeder = None
    leechers = []

    try:
        # start seeder
        seeder = PeerClient(
            torrent_path=torrent_path,
            download_dir=os.path.join(tmpdir, 'seeder'),
            listen_port=7001,
            peer_id='seeder-0',
            piece_selector=RarestFirst(),
            choking_manager=ChokingManager(),
            is_seeder=True,
            source_file=test_file,
        )
        await seeder.start()

        # give tracker a moment to register the seeder
        await asyncio.sleep(0.5)

        # start leechers
        for i in range(2):
            leecher = PeerClient(
                torrent_path=torrent_path,
                download_dir=os.path.join(tmpdir, f'leecher_{i}'),
                listen_port=7002 + i,
                peer_id=f'leecher-{i}',
                piece_selector=RarestFirst(),
                choking_manager=ChokingManager(),
                is_seeder=False,
            )
            await leecher.start()
            leechers.append(leecher)

        # run until leechers complete or timeout
        async def wait_for_leecher(leecher):
            return await leecher.run_until_complete(timeout=60)

        results = await asyncio.gather(
            *[wait_for_leecher(l) for l in leechers],
            return_exceptions=True,
        )

        # check results
        all_passed = True
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                print(f"  Leecher {i}: FAILED with error: {result}")
                all_passed = False
                continue

            if result['complete']:
                print(f"  Leecher {i}: completed in {result['elapsed_seconds']:.2f}s")

                # verify the downloaded file
                downloaded_file = os.path.join(tmpdir, f'leecher_{i}', 'testfile.bin')
                if os.path.exists(downloaded_file):
                    downloaded_hash = compute_file_hash(downloaded_file)
                    if downloaded_hash == original_hash:
                        print(f"  Leecher {i}: integrity check PASSED")
                    else:
                        print(f"  Leecher {i}: integrity check FAILED")
                        all_passed = False
                else:
                    print(f"  Leecher {i}: output file not found!")
                    all_passed = False
            else:
                print(f"  Leecher {i}: incomplete ({result['progress']}%)")
                all_passed = False

        print()
        if all_passed:
            print("E2E TEST PASSED")
        else:
            print("E2E TEST FAILED")

        return all_passed

    finally:
        # cleanup
        for l in leechers:
            try:
                await l.stop()
            except Exception:
                pass
        if seeder:
            try:
                await seeder.stop()
            except Exception:
                pass
        await tracker.stop()


if __name__ == '__main__':
    success = asyncio.run(run_e2e_test())
    sys.exit(0 if success else 1)
