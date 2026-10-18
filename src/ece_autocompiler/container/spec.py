"""Binary constants for the .ece container format (version 1).

This module is the executable reference for docs/ECE_FORMAT.md. Any change to
the byte layout must be reflected in both places.

File layout (all integers little-endian):

    +------------------+  offset 0
    | Header (96 B)    |
    +------------------+  manifest_offset
    | Manifest block   |  JSON, compressed (see compression_id)
    +------------------+  index_offset
    | Index block      |  JSON, compressed
    +------------------+  payload_offset
    | Payload blobs    |  entry data, each optionally compressed
    +------------------+  end of file
"""

from __future__ import annotations

import struct

# 8-byte signature. Mirrors the PNG trick (high bit + CRLF/SUB/LF) so that a
# file mangled by a text-mode transfer is rejected instead of mis-parsed.
MAGIC = b"\x89ECE\r\n\x1a\n"

# Container format generation. Major bumps are incompatible; minor bumps are
# backward compatible and readers must ignore unknown trailing header bytes.
FORMAT_MAJOR = 1
FORMAT_MINOR = 0
FORMAT_VERSION = FORMAT_MAJOR

# Fixed header size in bytes. Readers use header_size from the file, so future
# versions may grow the header while this field stays readable.
HEADER_SIZE = 96

# struct format for the v1 header. Field order matches _HEADER_FIELDS.
HEADER_STRUCT = struct.Struct("<8sHHIIBBHIQQQQQQQQI")
HEADER_FIELDS = (
    "magic",
    "major",
    "minor",
    "flags",
    "header_size",
    "compression_id",
    "hash_algo_id",
    "reserved_a",
    "entry_count",
    "manifest_offset",
    "manifest_length",
    "manifest_raw_length",
    "index_offset",
    "index_length",
    "index_raw_length",
    "payload_offset",
    "reserved_b",
    "reserved_c",
)
assert HEADER_STRUCT.size == HEADER_SIZE, HEADER_STRUCT.size

# Compression identifiers used for the manifest, index and payload entries.
COMPRESSION_NONE = 0
COMPRESSION_ZLIB = 1
COMPRESSION_LZMA = 2
COMPRESSION_NAMES = {
    COMPRESSION_NONE: "store",
    COMPRESSION_ZLIB: "zlib",
    COMPRESSION_LZMA: "lzma",
}
COMPRESSION_BY_NAME = {v: k for k, v in COMPRESSION_NAMES.items()}

# Hash algorithm identifiers. 0 means "no per-entry hash"; SHA-256 is default.
HASH_NONE = 0
HASH_SHA256 = 1
HASH_NAMES = {HASH_NONE: "none", HASH_SHA256: "sha256"}
HASH_BY_NAME = {v: k for k, v in HASH_NAMES.items()}
HASH_DIGEST_SIZE = {HASH_NONE: 0, HASH_SHA256: 32}

# Header flag bits.
FLAG_HAS_MANIFEST = 1 << 0

# zlib/lzma defaults. Level 6 is a good size/speed balance for build output.
ZLIB_LEVEL = 6
LZMA_PRESET = 6

# Sentinel for "no value" in 64-bit offset/length fields.
NO_VALUE = 0xFFFFFFFFFFFFFFFF
