"""
experiment.py - Automated experiment runner.

Runs the experiments described in our proposal:
  1. Compare piece selection strategies (rarest-first vs random vs sequential)
     across different swarm sizes and seeder counts
  2. Test free-rider detection with choking

Each config is run multiple times for statistical significance.
Results saved as JSON and plotted with matplotlib.
"""

import asyncio
import json
import os
import time
import random
import hashlib
import logging
import statistics

# experiments/ is next to src/, not inside it, so import through src.
# run from the repo root:  python -m experiments.experiment
from src.metainfo import metainfo
from src.metainfo.file_splitter import compute_file_hash
from src.tracker.tracker import TrackerServer
from src.peer.client import PeerClient
from src.strategy.piece_selector import RarestFirst, RandomSelect, SequentialSelect
from src.strategy.choking import ChokingManager

logger = logging.getLogger('experiment')

# experiment configs
STRATEGIES = {
    'rarest_first': RarestFirst,
    'random': RandomSelect,
    'sequential': SequentialSelect,
}


async def create_test_file(path, size_bytes):
    """Create a test file of specified size with random-ish data."""
    # use a seeded random for reproducibility, but write in chunks
    rng = random.Random(42)
    written = 0
    chunk_size = 1048576  # 1MB

    with open(path, 'wb') as f:
        while written < size_bytes:
            remaining = min(chunk_size, size_bytes - written)
            data = bytes(rng.getrandbits(8) for _ in range(remaining))
            f.write(data)
            written += remaining

    logger.info(f"Created test file: {path} ({size_bytes} bytes)")
    return path


async def run_single_experiment(config):
    """
    Run one experiment configuration.

    config dict:
        file_path: path to the test file
        torrent_path: path to the .torrent file
        strategy_name: which piece selection strategy
        num_seeders: number of seeders
        num_leechers: number of leechers
        tracker_port: port for tracker
        base_peer_port: starting port for peers
        rate_limit: upload rate limit in bytes/sec
        free_riders: number of free-riding leechers (never upload)
        timeout: max seconds to wait
    """
    strategy_name = config['strategy_name']
    num_seeders = config['num_seeders']
    num_leechers = config['num_leechers']
    tracker_port = config['tracker_port']
    base_port = config['base_peer_port']
    rate_limit = config.get('rate_limit', 0)
    num_free_riders = config.get('free_riders', 0)
    timeout = config.get('timeout', 120)

    logger.info(f"Running: strategy={strategy_name}, seeders={num_seeders}, "
                f"leechers={num_leechers}, free_riders={num_free_riders}")

    # start tracker
    tracker = TrackerServer('127.0.0.1', tracker_port)
    await tracker.start()

    clients = []
    tasks = []

    try:
        # create seeder clients
        for i in range(num_seeders):
            port = base_port + i
            selector = STRATEGIES[strategy_name]()
            choker = ChokingManager()

            client = PeerClient(
                torrent_path=config['torrent_path'],
                download_dir=os.path.join(config.get('work_dir', 'tmp'), f'seeder_{i}'),
                listen_port=port,
                peer_id=f"seeder-{i}",
                piece_selector=selector,
                choking_manager=choker,
                is_seeder=True,
                source_file=config['file_path'],
                rate_limit=rate_limit,
            )
            clients.append(('seeder', client))

        # create leecher clients
        for i in range(num_leechers):
            port = base_port + num_seeders + i
            selector = STRATEGIES[strategy_name]()
            choker = ChokingManager()

            # free riders get no piece_provider (they never upload)
            is_free_rider = (i < num_free_riders)

            client = PeerClient(
                torrent_path=config['torrent_path'],
                download_dir=os.path.join(config.get('work_dir', 'tmp'), f'leecher_{i}'),
                listen_port=port,
                peer_id=f"leecher-{i}" + ("-FR" if is_free_rider else ""),
                piece_selector=selector,
                choking_manager=choker,
                is_seeder=False,
                rate_limit=rate_limit,
                upload_enabled=not is_free_rider,
            )
            clients.append(('leecher', client))

        # start all clients
        for role, client in clients:
            await client.start()

        # Sample how evenly pieces are spread while peers download.
        availability_samples = []
        sample_start = time.monotonic()

        async def sample_availability():
            while True:
                counts = [
                    sum(bool(client.assembler.have[index]) for _, client in clients)
                    for index in range(clients[0][1].assembler.num_pieces)
                ]
                availability_samples.append({
                    'elapsed_seconds': round(time.monotonic() - sample_start, 2),
                    'variance': statistics.pvariance(counts) if counts else 0.0,
                })
                await asyncio.sleep(1)

        sampler_task = asyncio.create_task(sample_availability())

        # Run all peers together. Stop seeders once the leechers finish instead
        # of making every configuration wait for the full timeout.
        async def run_client(role, client):
            stats = await client.run_until_complete(timeout=timeout)
            return role, stats

        tasks = [
            (role, client, asyncio.create_task(run_client(role, client)))
            for role, client in clients
        ]
        leecher_tasks = [task for role, _, task in tasks if role == 'leecher']
        seeder_tasks = [task for role, _, task in tasks if role == 'seeder']

        leechers = [client for role, client in clients if role == 'leecher']
        finish_deadline = time.monotonic() + timeout + 5
        while (
            not all(client.assembler.complete for client in leechers)
            and time.monotonic() < finish_deadline
        ):
            await asyncio.sleep(0.1)

        for client in leechers:
            if not client.assembler.complete:
                logger.warning("Peer %s did not finish before the experiment timeout", client.peer_id)

        # A completed peer can still be inside a choking or socket-write wait.
        # Stop those loops based on the piece state we sampled above.
        for task in leecher_tasks:
            if not task.done():
                task.cancel()
        leecher_results = await asyncio.gather(*leecher_tasks, return_exceptions=True)

        for role, client in clients:
            if role == 'leecher' and client.assembler.complete and not client.assembler.assembled:
                try:
                    client.assembler.assemble()
                except OSError as exc:
                    logger.error("Could not assemble %s: %s", client.peer_id, exc)

        for task in seeder_tasks:
            task.cancel()
        await asyncio.gather(*seeder_tasks, return_exceptions=True)
        sampler_task.cancel()
        try:
            await sampler_task
        except asyncio.CancelledError:
            pass

        for task, result in zip(leecher_tasks, leecher_results):
            if isinstance(result, Exception):
                logger.error("Leecher task failed: %s", result)

        # collect stats
        experiment_result = {
            'config': {
                'strategy': strategy_name,
                'num_seeders': num_seeders,
                'num_leechers': num_leechers,
                'free_riders': num_free_riders,
                'rate_limit': rate_limit,
            },
            'peers': [],
            'availability_variance': availability_samples,
        }

        for role, client, task in tasks:
            stats = client.get_stats()
            stats['role'] = role
            experiment_result['peers'].append(stats)

        return experiment_result

    finally:
        # cleanup
        results = await asyncio.gather(
            *(
                asyncio.wait_for(client.stop(), timeout=10)
                for _, client in clients
            ),
            return_exceptions=True,
        )
        for (_, client), result in zip(clients, results):
            if isinstance(result, Exception):
                logger.warning("Could not stop peer %s cleanly: %s", client.peer_id, result)
        try:
            await asyncio.wait_for(tracker.stop(), timeout=5)
        except asyncio.TimeoutError:
            logger.warning("Timed out stopping experiment tracker")


async def run_full_experiment(output_dir='experiments/results'):
    """
    Run the complete experiment suite as described in the proposal.

    - 100 MB file, 256 KB pieces
    - Swarm sizes: 6 and 8 peers
    - Seeder counts: 1 and 2
    - 3 strategies
    - 5 runs per config
    """
    os.makedirs(output_dir, exist_ok=True)

    file_size = 100 * 1024 * 1024  # 100 MB
    piece_length = 256 * 1024      # 256 KB
    num_runs = 5
    rate_limit = 5 * 1024 * 1024   # 5 MB/s per peer

    test_file = os.path.join(output_dir, 'testfile.bin')
    torrent_file = os.path.join(output_dir, 'testfile.torrent')

    # create test file
    if not os.path.exists(test_file):
        await create_test_file(test_file, file_size)

    # generate torrent
    tracker_port = 9000
    metainfo.generate(
        test_file,
        f'http://127.0.0.1:{tracker_port}',
        piece_length=piece_length,
        output_path=torrent_file,
    )

    original_hash = compute_file_hash(test_file)

    all_results = []

    configs = []
    for strategy_name in ['rarest_first', 'random', 'sequential']:
        for total_peers, num_seeders in [(6, 1), (6, 2), (8, 1), (8, 2)]:
            num_leechers = total_peers - num_seeders
            configs.append({
                'strategy_name': strategy_name,
                'num_seeders': num_seeders,
                'num_leechers': num_leechers,
                'tracker_port': tracker_port,
                'base_peer_port': 6881,
                'rate_limit': rate_limit,
                'file_path': test_file,
                'torrent_path': torrent_file,
                'work_dir': os.path.join(output_dir, 'tmp'),
                'timeout': 300,
            })

    for run_num in range(num_runs):
        logger.info(f"=== Run {run_num + 1}/{num_runs} ===")

        for config in configs:
            config['tracker_port'] = tracker_port
            # use different ports for each run to avoid conflicts
            config['base_peer_port'] = 6881 + (run_num * 20)

            try:
                result = await run_single_experiment(config)
                result['run'] = run_num + 1
                all_results.append(result)
                logger.info(f"Config done: {config['strategy_name']}, "
                           f"s={config['num_seeders']}, l={config['num_leechers']}")
            except Exception as e:
                logger.error(f"Experiment failed: {e}")
                continue

            # small delay between configs
            await asyncio.sleep(2)

    # free-rider experiment
    logger.info("=== Free-rider experiment ===")
    for run_num in range(num_runs):
        config = {
            'strategy_name': 'rarest_first',
            'num_seeders': 1,
            'num_leechers': 7,
            'free_riders': 2,
            'tracker_port': tracker_port,
            'base_peer_port': 6881 + (run_num * 20),
            'rate_limit': rate_limit,
            'file_path': test_file,
            'torrent_path': torrent_file,
            'work_dir': os.path.join(output_dir, 'tmp'),
            'timeout': 300,
        }

        try:
            result = await run_single_experiment(config)
            result['run'] = run_num + 1
            result['experiment_type'] = 'free_rider'
            all_results.append(result)
        except Exception as e:
            logger.error(f"Free-rider experiment failed: {e}")

    # save results
    results_path = os.path.join(output_dir, 'results.json')
    with open(results_path, 'w') as f:
        json.dump(all_results, f, indent=2)

    logger.info(f"Results saved to {results_path}")
    return all_results


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s')
    asyncio.run(run_full_experiment())
