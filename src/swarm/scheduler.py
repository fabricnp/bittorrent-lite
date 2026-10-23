def select_rarest_piece(piece_manager, peer_id):
    """
    Choose the next piece to request from `peer_id`.

    Among the pieces the peer can give us (not downloaded, not in
    flight), pick the one held by the fewest connected peers. Ties go
    to the lowest piece index so the choice is deterministic.
    Returns None if there is nothing to request from this peer.
    """
    candidates = piece_manager.requestable_pieces(peer_id)

    if not candidates:
        return None

    availability = piece_manager.availability()

    return min(candidates, key=lambda index: (availability[index], index))
