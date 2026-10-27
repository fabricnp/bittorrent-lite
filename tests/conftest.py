from types import SimpleNamespace

import pytest

from src.metainfo.metainfo import create_metainfo
from src.swarm.peer_manager import PeerManager
from src.swarm.peer_server import PeerServer
from src.tracker.tracker import TrackerServer

SWARM_ID = "phase4-swarm"
PIECE_SIZE = 64


@pytest.fixture
def swarm_file(tmp_path):
    """A source file of 20 full pieces plus one short final piece."""
    data = bytes((i * 7 + 3) % 256 for i in range(PIECE_SIZE * 20 + 17))
    path = tmp_path / "source.bin"
    path.write_bytes(data)

    return SimpleNamespace(
        data=data,
        path=str(path),
        metainfo=create_metainfo(str(path), piece_size=PIECE_SIZE),
    )


@pytest.fixture
def start_server(swarm_file):
    servers = []

    def _start(peer_id, have=None, swarm_id=SWARM_ID):
        server = PeerServer(
            peer_id,
            swarm_id,
            swarm_file.metainfo,
            swarm_file.path,
            have=have,
        )
        server.port = server.start()
        servers.append(server)

        return server

    yield _start

    for server in servers:
        server.stop()


@pytest.fixture
def make_manager(swarm_file, tmp_path):
    managers = []

    def _make(peer_id="leecher", swarm_id=SWARM_ID, **kwargs):
        manager = PeerManager(
            peer_id,
            swarm_id,
            swarm_file.metainfo,
            str(tmp_path / f"pieces_{peer_id}"),
            **kwargs,
        )
        managers.append(manager)

        return manager

    yield _make

    for manager in managers:
        manager.close()


@pytest.fixture
def tracker_server():
    import asyncio
    import threading

    loop = asyncio.new_event_loop()
    ready = threading.Event()

    server = TrackerServer(port=0, verbose=False)

    def run_tracker():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.start())
        server.port = server.server.sockets[0].getsockname()[1]
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=run_tracker, daemon=True)
    thread.start()

    if not ready.wait(timeout=5):
        raise RuntimeError("Tracker server failed to start")

    yield server

    async def shutdown():
        await server.stop()

    asyncio.run_coroutine_threadsafe(shutdown(), loop).result(timeout=5)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)
    loop.close()
