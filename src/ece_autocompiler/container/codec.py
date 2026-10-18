"""Compression helpers shared by the writer, reader and extractor.

Every decompression entry point takes a hard ``max_output`` bound. That bound
is always smaller than a deliberate decompression bomb can produce, so a
malicious or corrupt block fails fast instead of exhausting memory.
"""

from __future__ import annotations

import lzma
import zlib

from ..errors import ContainerFormatError
from .spec import (
    COMPRESSION_LZMA,
    COMPRESSION_NONE,
    COMPRESSION_ZLIB,
    LZMA_PRESET,
    ZLIB_LEVEL,
)

_CHUNK = 64 * 1024


def compress(data: bytes, algo: int, level: int = ZLIB_LEVEL) -> bytes:
    if algo == COMPRESSION_NONE:
        return data
    if algo == COMPRESSION_ZLIB:
        return zlib.compress(data, level)
    if algo == COMPRESSION_LZMA:
        return lzma.compress(data, preset=LZMA_PRESET)
    raise ContainerFormatError("Unknown compression algorithm", detail=str(algo))


def decompress(data: bytes, algo: int, *, max_output: int) -> bytes:
    """Decompress *data*, refusing to produce more than *max_output* bytes."""
    if max_output < 0:
        raise ContainerFormatError("Negative decompression limit")

    if algo == COMPRESSION_NONE:
        if len(data) > max_output:
            raise ContainerFormatError("Stored block exceeds the size limit")
        return data

    if algo == COMPRESSION_ZLIB:
        out = _inflate_zlib(data, max_output)
    elif algo == COMPRESSION_LZMA:
        out = _inflate_lzma(data, max_output)
    else:
        raise ContainerFormatError("Unknown compression algorithm", detail=str(algo))

    if len(out) > max_output:
        raise ContainerFormatError("Decompressed block exceeds the size limit")
    return out


def _fail(exc: Exception, message: str) -> None:
    raise ContainerFormatError(message, detail=str(exc)) from exc


def _inflate_zlib(data: bytes, max_output: int) -> bytearray:
    decoder = zlib.decompressobj()
    out = bytearray()
    buf = data

    while True:
        budget = max_output - len(out) + 1
        if budget <= 0:
            raise ContainerFormatError("Decompressed block exceeds the size limit")
        try:
            piece = decoder.decompress(buf, budget)
        except zlib.error as exc:
            _fail(exc, "zlib block is corrupt")
        out += piece
        if len(out) > max_output:
            raise ContainerFormatError("Decompressed block exceeds the size limit")

        if decoder.unconsumed_tail:
            buf = decoder.unconsumed_tail
            continue

        if not buf:
            break

        # All input consumed; request any output still held internally.
        buf = b""

    while not decoder.eof:
        budget = max_output - len(out) + 1
        if budget <= 0:
            raise ContainerFormatError("Decompressed block exceeds the size limit")
        try:
            piece = decoder.decompress(b"", budget)
        except zlib.error as exc:
            _fail(exc, "zlib block is corrupt")
        if not piece:
            break
        out += piece
        if len(out) > max_output:
            raise ContainerFormatError("Decompressed block exceeds the size limit")

    if not decoder.eof:
        raise ContainerFormatError("Compressed block is truncated")
    return out


def _inflate_lzma(data: bytes, max_output: int) -> bytearray:
    decoder = lzma.LZMADecompressor()
    out = bytearray()
    buf = data

    while True:
        budget = max_output - len(out) + 1
        if budget <= 0:
            raise ContainerFormatError("Decompressed block exceeds the size limit")
        try:
            piece = decoder.decompress(buf, budget)
        except lzma.LZMAError as exc:
            _fail(exc, "lzma block is corrupt")
        out += piece
        if len(out) > max_output:
            raise ContainerFormatError("Decompressed block exceeds the size limit")

        if decoder.eof:
            break
        if decoder.needs_input or not buf:
            if decoder.needs_input:
                break
            buf = b""

    if not decoder.eof:
        raise ContainerFormatError("Compressed block is truncated")
    return out


def new_stream_encoder(algo: int, level: int = ZLIB_LEVEL):
    """Return an object with ``feed(bytes) -> bytes`` and ``flush() -> bytes``.

    Used by the writer so that a payload is compressed without ever being held
    entirely in memory.
    """
    return _StreamEncoder(algo, level)


class _StreamEncoder:
    def __init__(self, algo: int, level: int) -> None:
        self.algo = algo
        if algo == COMPRESSION_NONE:
            self._zlib = None
            self._lzma = None
        elif algo == COMPRESSION_ZLIB:
            self._zlib = zlib.compressobj(level)
            self._lzma = None
        elif algo == COMPRESSION_LZMA:
            self._zlib = None
            self._lzma = lzma.LZMACompressor(format=lzma.FORMAT_XZ, preset=LZMA_PRESET)
        else:
            raise ContainerFormatError("Unknown compression algorithm", detail=str(algo))

    def feed(self, data: bytes) -> bytes:
        if self.algo == COMPRESSION_NONE:
            return data
        if self._zlib is not None:
            return self._zlib.compress(data)
        assert self._lzma is not None
        return self._lzma.compress(data)

    def flush(self) -> bytes:
        if self.algo == COMPRESSION_NONE:
            return b""
        if self._zlib is not None:
            return self._zlib.flush()
        assert self._lzma is not None
        return self._lzma.flush()


def decompress_stream(
    algo: int,
    chunks,
    expected_size: int,
    *,
    read_chunk: int = _CHUNK,
):
    """Yield uncompressed chunks of a single entry without buffering it all.

    *chunks* is an iterator of raw (compressed) bytes. ``expected_size`` is the
    authoritative size taken from the index; producing more or fewer bytes than
    that is treated as corruption. This lets the extractor write arbitrarily
    large files while holding only a bounded amount of memory.
    """
    if expected_size < 0:
        raise ContainerFormatError("Negative expected size")

    if algo == COMPRESSION_NONE:
        total = 0
        for chunk in chunks:
            total += len(chunk)
            if total > expected_size:
                raise ContainerFormatError("Entry is larger than its recorded size")
            yield chunk
        if total != expected_size:
            raise ContainerFormatError(
                "Entry size mismatch", detail=f"{total} != {expected_size}"
            )
        return

    if algo == COMPRESSION_ZLIB:
        yield from _inflate_stream_zlib(chunks, expected_size)
    elif algo == COMPRESSION_LZMA:
        yield from _inflate_stream_lzma(chunks, expected_size)
    else:
        raise ContainerFormatError("Unknown compression algorithm", detail=str(algo))


def _inflate_stream_zlib(chunks, expected_size: int):
    decoder = zlib.decompressobj()
    total = 0
    exhausted_input = False

    for chunk in chunks:
        data = chunk
        while True:
            budget = expected_size - total + 1
            if budget <= 0:
                raise ContainerFormatError("Entry expands beyond its recorded size")
            try:
                out = decoder.decompress(data, budget)
            except zlib.error as exc:
                raise ContainerFormatError("zlib entry is corrupt", detail=str(exc)) from exc
            if out:
                total += len(out)
                if total > expected_size:
                    raise ContainerFormatError("Entry expands beyond its recorded size")
                yield out
            if decoder.unconsumed_tail:
                data = decoder.unconsumed_tail
                continue
            if not out:
                break
            data = b""
            if not decoder.unconsumed_tail:
                break

        if decoder.eof:
            exhausted_input = True
            break

    if not exhausted_input:
        while not decoder.eof:
            budget = expected_size - total + 1
            if budget <= 0:
                raise ContainerFormatError("Entry expands beyond its recorded size")
            try:
                out = decoder.decompress(b"", budget)
            except zlib.error as exc:
                raise ContainerFormatError("zlib entry is corrupt", detail=str(exc)) from exc
            if not out:
                break
            total += len(out)
            if total > expected_size:
                raise ContainerFormatError("Entry expands beyond its recorded size")
            yield out

    if not decoder.eof:
        raise ContainerFormatError("Compressed entry is truncated")
    if total != expected_size:
        raise ContainerFormatError(
            "Entry size mismatch", detail=f"{total} != {expected_size}"
        )


def _inflate_stream_lzma(chunks, expected_size: int):
    decoder = lzma.LZMADecompressor()
    total = 0
    saw_eof = False

    for chunk in chunks:
        data = chunk
        while data or (not decoder.needs_input and not decoder.eof):
            budget = expected_size - total + 1
            if budget <= 0:
                raise ContainerFormatError("Entry expands beyond its recorded size")
            try:
                out = decoder.decompress(data, budget)
            except lzma.LZMAError as exc:
                raise ContainerFormatError("lzma entry is corrupt", detail=str(exc)) from exc
            data = b""
            if out:
                total += len(out)
                if total > expected_size:
                    raise ContainerFormatError("Entry expands beyond its recorded size")
                yield out
            else:
                break
            if decoder.eof:
                break
        if decoder.eof:
            saw_eof = True
            break

    if not saw_eof:
        while not decoder.eof:
            budget = expected_size - total + 1
            if budget <= 0:
                raise ContainerFormatError("Entry expands beyond its recorded size")
            try:
                out = decoder.decompress(b"", budget)
            except lzma.LZMAError as exc:
                raise ContainerFormatError("lzma entry is corrupt", detail=str(exc)) from exc
            if not out:
                break
            total += len(out)
            if total > expected_size:
                raise ContainerFormatError("Entry expands beyond its recorded size")
            yield out

    if not decoder.eof:
        raise ContainerFormatError("Compressed entry is truncated")
    if total != expected_size:
        raise ContainerFormatError(
            "Entry size mismatch", detail=f"{total} != {expected_size}"
        )
