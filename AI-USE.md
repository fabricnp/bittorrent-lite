# AI Use Declaration — Member A

**Student:** Bibek Mishra
**Project:** BitTorrent-lite, CS-30003 Coding Assignment 1
**Contribution covered:** metainfo, piece hashing, file splitting and assembly

## AI assistance

I used ChatGPT during development of the codebase to review implementation, find integration problems, and suggest code fixes. I also used it to draft and revise the README and project documentation. I checked the suggestions against the repository and kept or adapted the ones that fit the project.

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
