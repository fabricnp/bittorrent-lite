from src.swarm.piece_manager import PieceManager
from src.swarm.scheduler import select_rarest_piece


def _manager(piece_count, peers):
    manager = PieceManager(piece_count)

    for peer_id, held in peers.items():
        manager.add_peer(
            peer_id,
            [index in held for index in range(piece_count)],
        )

    return manager


def test_picks_the_rarest_piece():
    manager = _manager(4, {
        "a": {0, 1, 2, 3},
        "b": {0, 1, 2},
        "c": {0, 1},
    })

    assert select_rarest_piece(manager, "a") == 3


def test_ties_go_to_the_lowest_index():
    manager = _manager(4, {"a": {0, 1, 2, 3}})

    assert select_rarest_piece(manager, "a") == 0


def test_choice_is_deterministic():
    manager = _manager(6, {"a": {0, 1, 2, 3, 4, 5}, "b": {1, 3}})

    picks = {select_rarest_piece(manager, "a") for _ in range(20)}

    assert picks == {0}


def test_only_considers_pieces_the_peer_has():
    manager = _manager(4, {"a": {0, 1}, "b": {2, 3}, "c": {0, 1, 2}})

    assert select_rarest_piece(manager, "a") == 0
    assert select_rarest_piece(manager, "b") == 3


def test_ignores_downloaded_pieces():
    manager = _manager(3, {"a": {0, 1, 2}, "b": {0, 1}})
    manager.mark_downloaded(2)

    assert select_rarest_piece(manager, "a") == 0


def test_ignores_in_flight_pieces():
    manager = _manager(3, {"a": {0, 1, 2}, "b": {0, 1}})
    manager.mark_in_flight(2, "a")

    assert select_rarest_piece(manager, "a") == 0


def test_returns_none_when_peer_has_nothing_useful():
    manager = _manager(3, {"a": {0}, "b": {1, 2}})
    manager.mark_downloaded(0)

    assert select_rarest_piece(manager, "a") is None


def test_returns_none_for_unknown_peer():
    assert select_rarest_piece(_manager(3, {"a": {0}}), "ghost") is None


def test_returns_none_when_everything_is_done():
    manager = _manager(2, {"a": {0, 1}})
    manager.mark_downloaded(0)
    manager.mark_in_flight(1, "a")

    assert select_rarest_piece(manager, "a") is None


def test_have_message_changes_the_choice():
    manager = _manager(3, {"a": {0, 1, 2}, "b": {0, 2}, "c": {0, 2}})

    assert select_rarest_piece(manager, "a") == 1

    manager.update_peer_have("b", 1)
    manager.update_peer_have("c", 1)

    assert select_rarest_piece(manager, "a") == 0


def test_losing_a_peer_makes_its_pieces_rarer():
    manager = _manager(3, {"a": {0, 1, 2}, "b": {0, 1}, "c": {0, 2}})

    assert select_rarest_piece(manager, "a") == 1

    manager.remove_peer("c")

    assert select_rarest_piece(manager, "a") == 2


def test_full_rarest_first_download_order():
    """Repeatedly picking and completing pieces goes rarest to commonest."""
    manager = _manager(5, {
        "a": {0, 1, 2, 3, 4},
        "b": {0, 1, 2, 3},
        "c": {0, 1, 2},
        "d": {0, 1},
    })

    order = []

    while (index := select_rarest_piece(manager, "a")) is not None:
        order.append(index)
        manager.mark_downloaded(index)

    assert order == [4, 3, 2, 0, 1]
