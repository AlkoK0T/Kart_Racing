"""Shared worker guards: no duplicate starts and no false stopped messages."""


def already_running(module):
    thread = getattr(module, '_thread', None)
    if thread is not None and thread.is_alive():
        if module._stop.is_set():
            raise RuntimeError(f'{module.name}: previous worker is still stopping')
        return True
    return False


def join_worker(module, timeout=3):
    thread = getattr(module, '_thread', None)
    if thread is not None:
        if thread.ident is not None or thread.is_alive():
            thread.join(timeout=timeout)
        if thread.is_alive():
            raise RuntimeError(f'{module.name}: worker did not stop within {timeout}s')
        module._thread = None
