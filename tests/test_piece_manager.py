import pytest

from src.swarm.piece_manager import PieceManager


def _manager_with_peers():
    """6 pieces; peer-a has 0-3, peer-b has 2-5."""
    manager = PieceManager(6)
    manager.add_peer("peer-a", [True, True, True, True, False, False])
    manager.add_peer("peer-b", [False, False, True, True, True, True])

    return manager


def test_starts_with_no_pieces():
    manager = PieceManager(4)

    assert manager.local_bitfield() == [False] * 4
    assert not manager.is_complete()
    assert not manager.has_piece(0)


def test_initial_have_is_copied():
    have = [True, False, True]
    manager = PieceManager(3, have=have)

    have[1] = True

    assert manager.local_bitfield() == [True, False, True]


def test_have_length_must_match_piece_count():
    with pytest.raises(ValueError):
        PieceManager(3, have=[True])


def test_mark_downloaded_updates_local_pieces_until_complete():
    manager = PieceManager(3)

    for index in range(3):
        assert not manager.is_complete()
        manager.mark_downloaded(index)
        assert manager.has_piece(index)

    assert manager.is_complete()


def test_zero_pieces_is_complete():
    assert PieceManager(0).is_complete()


def test_availability_counts_connected_peers():
    assert _manager_with_peers().availability() == [1, 1, 2, 2, 1, 1]


def test_availability_without_peers_is_all_zero():
    assert PieceManager(3).availability() == [0, 0, 0]


def test_have_message_increases_availability():
    manager = _manager_with_peers()

    manager.update_peer_have("peer-a", 5)

    assert manager.availability() == [1, 1, 2, 2, 1, 2]


def test_have_from_unknown_peer_is_rejected():
    with pytest.raises(ValueError):
        PieceManager(3).update_peer_have("ghost", 0)


def test_bitfield_length_must_match_piece_count():
    with pytest.raises(ValueError):
        PieceManager(3).add_peer("peer-a", [True, False])


def test_remove_peer_lowers_availability():
    manager = _manager_with_peers()

    manager.remove_peer("peer-a")

    assert manager.availability() == [0, 0, 1, 1, 1, 1]


def test_remove_peer_releases_only_its_in_flight_pieces():
    manager = _manager_with_peers()
    manager.mark_in_flight(0, "peer-a")
    manager.mark_in_flight(1, "peer-a")
    manager.mark_in_flight(4, "peer-b")

    assert manager.remove_peer("peer-a") == [0, 1]
    assert manager.in_flight_owner(0) is None
    assert manager.in_flight_owner(1) is None
    assert manager.in_flight_owner(4) == "peer-b"


def test_remove_unknown_peer_is_harmless():
    assert PieceManager(3).remove_peer("ghost") == []


def test_in_flight_tracking_per_peer():
    manager = _manager_with_peers()

    manager.mark_in_flight(0, "peer-a")
    manager.mark_in_flight(1, "peer-a")
    manager.mark_in_flight(4, "peer-b")

    assert manager.in_flight_count("peer-a") == 2
    assert manager.in_flight_count("peer-b") == 1
    assert manager.in_flight_owner(0) == "peer-a"


def test_piece_cannot_be_in_flight_twice():
    manager = _manager_with_peers()
    manager.mark_in_flight(2, "peer-a")

    with pytest.raises(ValueError):
        manager.mark_in_flight(2, "peer-b")


def test_downloaded_piece_cannot_be_requested():
    manager = _manager_with_peers()
    manager.mark_downloaded(0)

    with pytest.raises(ValueError):
        manager.mark_in_flight(0, "peer-a")


def test_cannot_request_piece_the_peer_lacks():
    manager = _manager_with_peers()

    with pytest.raises(ValueError):
        manager.mark_in_flight(5, "peer-a")


def test_cannot_request_from_unknown_peer():
    with pytest.raises(ValueError):
        PieceManager(3).mark_in_flight(0, "ghost")


def test_mark_downloaded_clears_in_flight():
    manager = _manager_with_peers()
    manager.mark_in_flight(0, "peer-a")

    manager.mark_downloaded(0)

    assert manager.in_flight_count("peer-a") == 0
    assert manager.has_piece(0)


def test_release_in_flight_makes_piece_requestable_again():
    manager = _manager_with_peers()
    manager.mark_in_flight(0, "peer-a")

    manager.release_in_flight(0)

    assert 0 in manager.requestable_pieces("peer-a")


def test_requestable_pieces_are_what_the_peer_has():
    manager = _manager_with_peers()

    assert manager.requestable_pieces("peer-a") == [0, 1, 2, 3]
    assert manager.requestable_pieces("peer-b") == [2, 3, 4, 5]


def test_requestable_pieces_skip_downloaded_and_in_flight():
    manager = _manager_with_peers()
    manager.mark_downloaded(0)
    manager.mark_in_flight(2, "peer-b")

    assert manager.requestable_pieces("peer-a") == [1, 3]
    assert manager.requestable_pieces("peer-b") == [3, 4, 5]


def test_requestable_pieces_for_unknown_peer_is_empty():
    assert _manager_with_peers().requestable_pieces("ghost") == []
