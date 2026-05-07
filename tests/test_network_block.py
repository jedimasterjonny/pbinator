import socket

import pytest


def test_socket_creation_is_blocked() -> None:
    with pytest.raises(RuntimeError):
        socket.socket()


@pytest.mark.usefixtures("allow_socket")
def test_allow_socket_fixture_restores_sockets() -> None:
    with socket.socket() as sock:
        assert sock.fileno() != -1
