# AI Use Declaration — Member A

**Student:** Bibek Mishra
**Project:** BitTorrent-lite, CS-30003 Coding Assignment 1
**Contribution covered:** metainfo, piece hashing, file splitting and assembly

## AI assistance

I used ChatGPT during development of the codebase to review implementation, find integration problems, and suggest code fixes. I also used it to draft and revise the README and project documentation. I checked the suggestions against the repository and kept or adapted the ones that fit the project.

During the demo, the tracker stopped listing a seeder after its announce expired, and Ctrl+C did not stop the seeder process in Windows. ChatGPT helped me trace both problems. I updated the seeder to refresh its tracker entry every 30 seconds and identify itself as a seeder, then changed its shutdown wait so Windows can handle Ctrl+C. I confirmed the shutdown behavior manually and ran the full test suite; all 277 tests passed.

While preparing the experiment, ChatGPT helped me compare the harness with the plan and find gaps: it did not record piece-availability variance, free-rider peers could still upload, and rarest-first was not getting updated piece counts. I updated the harness and peer code to collect variance samples, block free-rider uploads, update availability as peers exchange bitfields and pieces, and apply each peer's upload limit across its connections. I also fixed cleanup so completed runs stop their peer tasks and sockets.

The first full run completed all 60 strategy comparisons and five free-rider runs, but six final-file renames failed intermittently on Windows with `WinError 5`. I added a short retry around the rename and checked it in an eight-peer run after simulating three temporary file locks; all six downloaded files matched the source hash. Plot generation also failed at first because the plotting packages were missing from `requirements.txt`, and the installed Matplotlib version had changed the box-plot argument name. I added the dependencies, updated the plot calls, and generated all four plots and the summary CSV.

The results do not show rarest-first as the fastest strategy in every configuration. All ten free-rider peers finished and uploaded zero bytes, but they completed faster on average than the regular peers in this setup. The choker logged the free riders repeatedly; the single seeder still provided enough data for them to finish, so these results do not show that choking reduced their completion speed. I checked the raw JSON and plots before writing this summary.

## Reference material

These sources were checked during AI-assisted review of the metainfo and hashing implementation. The BitTorrent specification describes the standard protocol; this class project is simplified and uses SHA-256 for piece hashes.

1. **Bram Cohen, BitTorrent Protocol Specification (BEP 3).** Background on metainfo, bencoding, piece hashes, info hashes, trackers and peer messages.
   https://www.bittorrent.org/beps/bep_0003.html
2. **Python documentation: hashlib.** Reference for Python’s SHA-256 hash functions.
   https://docs.python.org/3/library/hashlib.html
3. **NIST, FIPS 180-4: Secure Hash Standard.** Standard defining SHA-256 and related hash functions.
   https://csrc.nist.gov/pubs/fips/180-4/upd1/final
4. **Computer Basics, “What is the BitTorrent Protocol and How Do Torrents Work?”** Introductory video on peers, seeders, leechers and trackers.
   https://www.youtube.com/watch?v=y3k6SRKXbX0
5. **Explaining Tech, “What are the BitTorrent and DHT Protocols and HOW do they work?”** Introductory video on the BitTorrent swarm and DHT. DHT is background only; this project does not implement it.
   https://www.youtube.com/watch?v=xg0RHkuTwlQ

The sources were used to check technical details; no source code was copied from them.
