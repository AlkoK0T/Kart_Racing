"""Process-local ownership of COM ports shared by serial sources."""
from contextlib import contextmanager
from threading import RLock

_lock = RLock()
_owners = {}


@contextmanager
def claim_port(port, owner):
    key = port.strip().casefold().removeprefix('\\\\.\\')
    with _lock:
        if key in _owners and _owners[key] is not owner:
            raise OSError(f'COM port {port} is already owned by another module')
        _owners[key] = owner
    try:
        yield
    finally:
        with _lock:
            if _owners.get(key) is owner:
                del _owners[key]
