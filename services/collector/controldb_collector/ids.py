"""Server-side ID generators (mirrors SDK ulid format)."""

import os
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _ulid() -> str:
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ms << 80) | rand
    chars = []
    for _ in range(26):
        chars.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def generate_run_id() -> str:
    return f"run_{_ulid()}"


def generate_event_id() -> str:
    return f"evt_{_ulid()}"


def generate_approval_id() -> str:
    return f"appr_{_ulid()}"


def generate_export_id() -> str:
    return f"exp_{_ulid()}"
