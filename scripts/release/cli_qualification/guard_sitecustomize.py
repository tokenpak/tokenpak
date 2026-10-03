import socket


def _deny(*a, **k):
    raise OSError("QUALIFICATION-GUARD: outbound socket blocked")


socket.socket.connect = _deny
socket.socket.connect_ex = _deny
socket.getaddrinfo = _deny
socket.create_connection = _deny
try:
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

    def _k(*a, **k):
        raise RuntimeError("QUALIFICATION-GUARD: key generation blocked")

    rsa.generate_private_key = _k
    ed25519.Ed25519PrivateKey.generate = _k
    ec.generate_private_key = _k
except ImportError:
    pass
