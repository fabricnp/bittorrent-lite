import socket


class ConnectionClosed(Exception):
    """Raised when the remote peer closes the TCP connection."""


class PeerConnection:

    def __init__(self, sock: socket.socket):
        self.sock = sock

    def send_bytes(self, data: bytes):
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")

        self.sock.sendall(data)

    def receive_exactly(self, size: int) -> bytes:
        """
        Receive exactly `size` bytes unless the connection closes.

        TCP is a byte stream, so one recv() call is not guaranteed
        to return the requested number of bytes.
        """

        if size < 0:
            raise ValueError("size cannot be negative")

        data = bytearray()

        while len(data) < size:
            chunk = self.sock.recv(size - len(data))

            if not chunk:
                raise ConnectionClosed(
                    "Remote peer closed the connection"
                )

            data.extend(chunk)

        return bytes(data)

    def receive_available(self, size: int = 4096) -> bytes:
        if size <= 0:
            raise ValueError("size must be positive")

        data = self.sock.recv(size)

        if not data:
            raise ConnectionClosed(
                "Remote peer closed the connection"
            )

        return data

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

        self.sock.close()