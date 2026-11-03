# BitTorrent-lite

This is our CS-30003 Coding Assignment 1 project. We built a small peer-to-peer file transfer system in Python to try out the main ideas behind BitTorrent: a tracker helps peers find each other, peers exchange file pieces over TCP, and the receiver checks each piece before saving it.

## Team

| Member | Name | Main contribution |
| --- | --- | --- |
| A | Bibek Mishra | Metainfo, file splitting and assembly, hashing |
| B | Pravesh Basnet | Tracker and swarm state |
| C | Saurav Neupane | Peer wire protocol |
| D | Sworup Bastola | Piece selection, choking, experiments and analysis |

## How a transfer works

1. The file is split into pieces and a SHA-256 hash is saved for each piece.
2. A seeder announces itself to the tracker.
3. A leecher asks the tracker for peers, then connects to a seeder directly.
4. The leecher requests pieces, checks their hashes, and assembles the file.

The tracker shares peer addresses; file data travels between peers.

## Project layout

- **src/metainfo** — metainfo, piece hashing, and file assembly
- **src/tracker** — HTTP tracker, request parsing, and swarm state
- **src/peer** — peer messages and connections
- **src/strategy** — piece selection and choking
- **src/swarm** — seeding and downloading commands
- **experiments** — experiment runner and result plots
- **tests** — protocol, tracker, peer, and transfer tests
- **docs** — metainfo and protocol notes, plus the project proposal

## Run a small demo

Run these commands from the repository folder. Use three terminals and leave the tracker and seeder running while the leecher downloads.

### Terminal 1: start the tracker

~~~powershell
python -m src.tracker --port 7000
~~~

The tracker status is available at http://127.0.0.1:7000/stats. It shows an empty swarm until a peer announces.

### Terminal 2: prepare the file and start a seeder

~~~powershell
python -m src.metainfo mkfile demo_data/demo.bin 64K --seed 42
python -c "from src.metainfo.metainfo import create_metainfo, save_metainfo; m=create_metainfo('demo_data/demo.bin', piece_size=4096); save_metainfo(m, 'demo_data/demo.json'); print(f'{m.file_size} bytes, {m.piece_count} pieces')"
python -m src.swarm.node seed --peer-id seed --swarm-id demo --metainfo demo_data/demo.json --file demo_data/demo.bin
~~~

The file is 64 KB and has sixteen 4 KB pieces. The seeder command stays open while it serves the file.

### Terminal 3: download from the seeder

~~~powershell
python -m src.swarm.node leech --peer-id leecher --swarm-id demo --metainfo demo_data/demo.json --pieces-dir demo_data/pieces --output demo_data/downloaded.bin
~~~

The output should show the tracker returning the seeder, a peer handshake, piece requests, and verified pieces. Check that the original and downloaded files match:

~~~powershell
(Get-FileHash demo_data/demo.bin).Hash -eq (Get-FileHash demo_data/downloaded.bin).Hash
~~~

PowerShell should print True. Refresh the tracker status page to see its announce counts. Press Ctrl+C in the seeder and tracker terminals when finished.

## Experiments

The experiment runner compares rarest-first, random, and sequential piece selection with different swarm sizes and seeder counts. It records completion time and piece-availability variance, and includes a free-rider scenario.

~~~powershell
python -m experiments.experiment
python -m experiments.plot_results
~~~

The full experiment takes longer than the small demo. Its JSON results and plots are written under experiments/results.

## Tests

Run the test suite with:

~~~powershell
python -m pytest tests/ -v
~~~

## Scope

This is a teaching project, not a full BitTorrent client. It handles single-file transfers over local TCP connections. It does not implement DHT, NAT traversal, encryption, or multi-file torrents.

## References

- [BitTorrent protocol specification](https://www.bittorrent.org/beps/bep_0003.html)
- [Python asyncio documentation](https://docs.python.org/3/library/asyncio.html)
