"""Content hashing. One algorithm everywhere — landing and transformed rows
must be comparable across runs."""

import hashlib
from typing import BinaryIO

_CHUNK_SIZE_BYTES = 1 << 20


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_stream(stream: BinaryIO) -> str:
    hasher = hashlib.sha256()
    while data_chunk := stream.read(_CHUNK_SIZE_BYTES):
        hasher.update(data_chunk)
    return hasher.hexdigest()


def sha256_file(path: str) -> str:
    with open(path, "rb") as file_handle:
        return sha256_stream(file_handle)
