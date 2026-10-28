# BitTorrent-lite P2P File Distribution System

A peer-to-peer file distribution system built from scratch using Python asyncio and raw TCP sockets. Implements core BitTorrent concepts: tracker server, peer wire protocol, rarest-first piece selection, and choking/unchoking mechanisms.

## Team Members

- **Member A** (Bibek Mishra): Metainfo format, file splitter/assembler, hashing
- **Member B** (Pravesh Basnet): Tracker server (raw TCP HTTP, announce, swarm state)
- **Member C** (Saurav Neupane): Peer wire protocol (message framing, handshake, state machine)
- **Member D** (Sworup Bastola): Piece selection algorithms, choking logic, experiment harness, analysis

## Project Structure

```
bittorrent-lite/
├── src/
│   ├── metainfo/          # File splitting, hashing, metainfo format (Member A)
│   │   ├── metainfo.py
│   │   ├── assembler.py
│   │   ├── file_splitter.py
│   │   └── bencode.py
│   ├── tracker/           # Tracker server implementation (Member B)
│   │   ├── tracker.py
│   │   ├── http_request.py
│   │   └── swarm.py
│   ├── peer/              # Peer wire protocol (Member C)
│   │   ├── protocol.py
│   │   ├── peer_connection.py
│   │   └── client.py
│   ├── strategy/          # Piece selection & choking (Member D)
│   │   ├── piece_selector.py
│   │   └── choking.py
│   └── swarm/             # Multi-peer coordination
│       ├── piece_manager.py
│       └── scheduler.py
├── experiments/           # Experiment harness & analysis (Member D)
│   ├── experiment.py
│   └── plot_results.py
├── docs/                  # Protocol specifications
│   ├── protocol_spec.md   # Main specification (Members B & D)
│   ├── metainfo_format.md
│   └── peer_protocol.md
└── tests/                 # Unit tests
    └── test_*.py
```

## Requirements

- Python 3.12+
- matplotlib
- pandas
- numpy
- pytest (for tests)

Install dependencies:
```bash
pip install matplotlib pandas numpy pytest
```

## Quick Start

### 1. Run the Tracker

Start the tracker server on localhost:8000:

```bash
python -m src.tracker.tracker --host 127.0.0.1 --port 8000 --interval 30
```

Options:
- `--host`: Bind address (default: 127.0.0.1)
- `--port`: Port number (default: 8000)
- `--interval`: Seconds between peer re-announces (default: 30)
- `--timeout`: Drop quiet peers after N seconds (default: 120)

The tracker provides these endpoints:
- `GET /announce` - Peer registration and peer list
- `GET /scrape` - Swarm statistics
- `GET /stats` - Debug info (all swarms)

### 2. Create a Torrent File

Generate metainfo for a file:

```python
from src.metainfo import metainfo

metainfo.generate(
    file_path='myfile.bin',
    tracker_url='http://127.0.0.1:8000',
    piece_length=256 * 1024,  # 256 KB pieces
    output_path='myfile.torrent'
)
```

### 3. Run Peers

#### Seeder (has complete file):

```python
from src.peer.client import PeerClient
from src.strategy.piece_selector import RarestFirst
from src.strategy.choking import ChokingManager
import asyncio

client = PeerClient(
    torrent_path='myfile.torrent',
    download_dir='./seeder_data',
    listen_port=6881,
    peer_id='seeder-1',
    piece_selector=RarestFirst(),
    choking_manager=ChokingManager(),
    is_seeder=True,
    source_file='myfile.bin',  # Original file
)

async def run():
    await client.start()
    await client.run_until_complete(timeout=300)
    await client.stop()

asyncio.run(run())
```

#### Leecher (downloading):

```python
client = PeerClient(
    torrent_path='myfile.torrent',
    download_dir='./leecher_data',
    listen_port=6882,
    peer_id='leecher-1',
    piece_selector=RarestFirst(),
    choking_manager=ChokingManager(),
    is_seeder=False,
)

async def run():
    await client.start()
    await client.run_until_complete(timeout=300)
    await client.stop()

asyncio.run(run())
```

## Piece Selection Strategies

Three strategies are implemented (see `src/strategy/piece_selector.py`):

### 1. Rarest-First (`RarestFirst`)
- Selects the piece held by the fewest peers
- Improves swarm health by distributing rare pieces quickly
- **Expected to perform best**

### 2. Random Selection (`RandomSelect`)
- Picks a random available piece
- Baseline comparison, no coordination overhead

### 3. Sequential Selection (`SequentialSelect`)
- Downloads pieces in order (0, 1, 2, ...)
- Naive approach, **expected to perform worst**

## Choking Algorithm

Implemented in `src/strategy/choking.py`:

- **Regular unchoking**: Every 10s, unchoke the top 4 peers by upload contribution
- **Optimistic unchoking**: Every 30s, give a random choked peer a chance
- **Free-rider detection**: Logs peers with zero upload rate
- **Seeder behavior**: Seeders unchoke all interested peers (altruistic)

## Running Experiments

### Full Experiment Suite

Run all experiments defined in the proposal:

```bash
python -m experiments.experiment
```

This will:
1. Create a 100 MB test file
2. Generate metainfo with 256 KB pieces
3. Run 5 repetitions of each configuration:
   - 3 strategies × 2 swarm sizes (6, 8) × 2 seeder counts (1, 2)
4. Run free-rider experiments (2 non-uploading peers)
5. Save results to `experiments/results/results.json`

**Note**: Full suite takes ~30-60 minutes to complete.

### Generate Plots

After experiments complete:

```bash
python -m experiments.plot_results
```

Generates:
- `distribution_time.png` - Box plots of total distribution time by strategy
- `per_peer_completion.png` - Individual peer completion times
- `free_rider_impact.png` - Impact of choking on free-riders
- `summary.csv` - Statistical summary table

## Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_protocol.py -v

# Run with coverage
pytest tests/ --cov=src --cov-report=html
```

Key test files:
- `test_protocol.py` - Message encoding/decoding
- `test_pieces.py` - Piece management
- `test_tracker.py` - Tracker HTTP protocol
- `test_transfer.py` - Multi-peer scenarios
- `test_scheduler.py` - Piece selection

## Documentation

Detailed specifications:
- **`docs/protocol_spec.md`** - Complete protocol specification (tracker, peer wire, piece selection, choking)
- **`docs/metainfo_format.md`** - Torrent file format
- **`docs/peer_protocol.md`** - Peer wire protocol details

## Experimental Hypothesis

**Claim**: Rarest-first piece selection reduces total swarm distribution time compared with random and sequential selection, particularly as swarm size increases.

**Variables**:
- Independent: strategy, swarm size, seeder count
- Dependent: distribution time, per-peer completion time, piece availability variance

**Expected Results**:
1. Rarest-first < Random < Sequential (distribution time)
2. Larger swarms benefit more from rarest-first
3. Free-riders have longer completion times due to choking

## Architecture

```
┌─────────────┐
│   Tracker   │  HTTP/TCP server, manages swarm membership
└──────┬──────┘
       │ announce/peer-list
       │
   ┌───┴────────────┐
   │                │
┌──▼───┐        ┌───▼──┐
│Peer A│◄─TCP───►│Peer B│  Wire protocol: handshake, bitfield,
└──┬───┘        └───┬──┘  request, piece, choke, unchoke
   │                │
   │  ┌──────────┐  │
   └─►│  Pieces  │◄─┘     FileAssembler: verify & write pieces
      └──────────┘
```

**Each peer has**:
- **PeerClient**: Orchestrates tracker communication and peer connections
- **PeerConnection**: Manages one TCP connection (state machine)
- **FileAssembler**: Validates and stores received pieces
- **PieceSelector**: Chooses which piece to request next
- **ChokingManager**: Decides which peers to unchoke

## Key Features

✅ **From Scratch**: Raw TCP sockets (no HTTP library for tracker)  
✅ **Async**: Python asyncio for concurrent peer connections  
✅ **Verified**: SHA-256 per-piece and whole-file verification  
✅ **Pipelined**: Up to 5 outstanding requests per peer  
✅ **Rate Limited**: Token bucket algorithm for fair experiments  
✅ **Instrumented**: Comprehensive logging and statistics  

## Limitations

- **Localhost only**: No NAT traversal or DHT
- **No encryption**: Plain TCP
- **Single file**: No multi-file torrents
- **Simplified protocol**: No endgame mode, port, or extended messages
- **No persistence**: Peers don't resume interrupted downloads

## Performance Notes

- **Piece size**: 256 KB balances hash overhead vs. granularity
- **Max connections**: 6-8 peers (typical for local experiments)
- **Pipeline depth**: 5 requests (balances throughput vs. fairness)
- **Choking intervals**: 10s regular, 30s optimistic (standard BitTorrent)

## Troubleshooting

### "No module named pytest"
```bash
pip install pytest
```

### "Address already in use"
Change port numbers or wait for old processes to release ports:
```bash
# Find process using port 8000
lsof -i :8000
# Kill it
kill -9 <PID>
```

### "Connection refused" when peer tries to connect
Make sure tracker is running first.

### Experiments hang
- Check if any peers crashed (check logs)
- Reduce timeout in experiment config
- Start with smaller swarm (2-3 peers) for debugging

### Import errors
Make sure you're running from the repository root:
```bash
cd /path/to/bittorrent-lite
python -m experiments.experiment
```

## Contributing

This is a course project. Contributions are structured by team member responsibilities as defined in the proposal.

## License

Academic project for CS-30003 Coding Assignment 1.

## References

- [BitTorrent Protocol Specification](http://www.bittorrent.org/beps/bep_0003.html)
- [BitTorrent Economics Paper](http://bittorrent.org/bittorrentecon.pdf) (rarest-first analysis)
- Python asyncio documentation

---

**Status**: ✅ Complete and ready for experiments

All Member D deliverables implemented:
- ✅ Piece selection algorithms (3 strategies)
- ✅ Choking/unchoking logic
- ✅ Experiment harness
- ✅ Plotting and analysis
- ✅ Protocol specification (Section 3)
