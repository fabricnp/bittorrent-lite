#!/usr/bin/env python3
"""
Quick system test - verifies all Member D components work.
Run this before the full experiment suite.
"""

import asyncio
import os
import sys
import tempfile
import shutil

# Test imports
print("Testing imports...")
try:
    from src.metainfo import metainfo, FileAssembler
    from src.tracker.tracker import TrackerServer
    from src.peer.client import PeerClient
    from src.strategy.piece_selector import RarestFirst, RandomSelect, SequentialSelect
    from src.strategy.choking import ChokingManager
    from src.peer import protocol
    print("✓ All imports successful")
except ImportError as e:
    print(f"✗ Import failed: {e}")
    sys.exit(1)

# Test piece selectors
print("\nTesting piece selectors...")
try:
    # Test RarestFirst
    rf = RarestFirst()
    rf.update_availability([True, True, False, True])
    piece = rf.select(missing_pieces=[0, 2], peer_bitfield=[True, False, True, False], pending_requests=set())
    print(f"  RarestFirst selected piece: {piece}")
    
    # Test RandomSelect
    rs = RandomSelect()
    piece = rs.select(missing_pieces=[0, 1, 2], peer_bitfield=[True, True, True], pending_requests=set())
    print(f"  RandomSelect selected piece: {piece}")
    
    # Test SequentialSelect
    ss = SequentialSelect()
    piece = ss.select(missing_pieces=[2, 0, 1], peer_bitfield=[True, True, True], pending_requests=set())
    print(f"  SequentialSelect selected piece: {piece} (should be 0)")
    
    print("✓ Piece selectors working")
except Exception as e:
    print(f"✗ Piece selector test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test choking manager
print("\nTesting choking manager...")
try:
    cm = ChokingManager(max_unchoked=2, eval_interval=1, optimistic_interval=3)
    cm.register_peer('peer1')
    cm.register_peer('peer2')
    cm.register_peer('peer3')
    
    cm.record_upload('peer1', 1000)
    cm.record_upload('peer2', 500)
    # peer3 uploads nothing (free-rider)
    
    print("✓ Choking manager working")
except Exception as e:
    print(f"✗ Choking manager test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test protocol functions
print("\nTesting protocol functions...")
try:
    # Test handshake
    info_hash = b'0' * 32
    peer_id = 'test-peer-123'
    hs = protocol.build_handshake(info_hash, peer_id)
    print(f"  Built handshake: {len(hs)} bytes")
    
    # Test messages
    msg = protocol.build_have(5)
    print(f"  Built HAVE message: {len(msg)} bytes")
    
    msg = protocol.build_request(3, 0, 16384)
    print(f"  Built REQUEST message: {len(msg)} bytes")
    
    print("✓ Protocol functions working")
except Exception as e:
    print(f"✗ Protocol test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test metainfo generation
print("\nTesting metainfo generation...")
try:
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create test file
        test_file = os.path.join(tmpdir, 'test.bin')
        with open(test_file, 'wb') as f:
            f.write(b'X' * (256 * 1024 + 100))  # Just over 1 piece
        
        # Generate metainfo
        torrent_file = os.path.join(tmpdir, 'test.torrent')
        metainfo.generate(
            test_file,
            'http://127.0.0.1:8000',
            piece_length=256 * 1024,
            output_path=torrent_file
        )
        
        # Parse it back
        info = metainfo.parse(torrent_file)
        print(f"  Metainfo: {info['file_name']}, {len(info['piece_hashes'])} pieces")
        
        print("✓ Metainfo generation working")
except Exception as e:
    print(f"✗ Metainfo test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test tracker server
print("\nTesting tracker server...")
async def test_tracker():
    try:
        tracker = TrackerServer('127.0.0.1', 9999, verbose=False)
        await tracker.start()
        print(f"  Tracker started on port 9999")
        
        # Give it a moment
        await asyncio.sleep(0.5)
        
        await tracker.stop()
        print("  Tracker stopped")
        print("✓ Tracker working")
        return True
    except Exception as e:
        print(f"✗ Tracker test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

try:
    result = asyncio.run(test_tracker())
    if not result:
        sys.exit(1)
except Exception as e:
    print(f"✗ Async test failed: {e}")
    sys.exit(1)

print("\n" + "="*50)
print("✅ ALL TESTS PASSED")
print("="*50)
print("\nSystem is ready! You can now:")
print("  1. Run experiments: python -m experiments.experiment")
print("  2. Generate plots: python -m experiments.plot_results")
print("  3. Start tracker: python -m src.tracker.tracker")
