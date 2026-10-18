# The `.ece` container format (version 1)

`.ece` is a documented binary container for build output. It is **not** an
executable format. Integrity hashes are built in; an external-tool publisher
signature is optional (see [`signature`](#signature-optional-null-by-default)).
`container/spec.py` is the executable reference for the constants below; if you
change one, change both.

All integers are **little-endian**. All offsets are absolute byte offsets from
the start of the file.

## Overall layout

```
+--------------------+  offset 0
| Header (96 bytes)  |
+--------------------+  manifest_offset
| Manifest block     |  JSON, compressed
+--------------------+  index_offset
| Index block        |  JSON, compressed
+--------------------+  payload_offset
| Payload blobs      |  per entry, each optionally compressed
+--------------------+  end of file
```

The manifest and index are stored compressed using the header's
`compression_id`. Each payload entry may use its own compression, recorded in
the index.

## Magic

`MAGIC = 89 45 43 45 0D 0A 1A 0A` (bytes `\x89 E C E \r \n \x1a \n`).

The `0x89` high bit plus `\r\n\x1a\n` mirror the PNG signature so a file mangled
by a text-mode transfer is rejected rather than mis-parsed.

## Header (96 bytes)

`struct` format: `<8sHHIIBBHIQQQQQQQQI`.

| Offset | Type | Field | Notes |
| ---: | --- | --- | --- |
| 0 | `8s` | `magic` | `\x89ECE\r\n\x1a\n` |
| 8 | `H` | `major` | Format major version (1). Mismatch is fatal. |
| 10 | `H` | `minor` | Format minor version (0). Readers tolerate newer minors. |
| 12 | `I` | `flags` | Bit 0 (`FLAG_HAS_MANIFEST`) = manifest present. |
| 16 | `I` | `header_size` | 96 for v1. Readers honour this field. |
| 20 | `B` | `compression_id` | 0 store, 1 zlib, 2 lzma (manifest + index). |
| 21 | `B` | `hash_algo_id` | 0 none, 1 sha256. |
| 22 | `H` | `reserved_a` | Reserved, 0. |
| 24 | `I` | `entry_count` | Number of index entries. |
| 28 | `Q` | `manifest_offset` | |
| 36 | `Q` | `manifest_length` | Compressed length. |
| 44 | `Q` | `manifest_raw_length` | Uncompressed length. |
| 52 | `Q` | `index_offset` | |
| 60 | `Q` | `index_length` | Compressed length. |
| 68 | `Q` | `index_raw_length` | Uncompressed length. |
| 76 | `Q` | `payload_offset` | Start of payload; entry offsets are relative to this. |
| 84 | `Q` | `reserved_b` | Reserved, 0. |
| 92 | `I` | `reserved_c` | Reserved, 0. |

`NO_VALUE = 0xFFFFFFFFFFFFFFFF` marks an absent offset/length.

### Why entry offsets are relative
An index entry's `offset` is measured from `payload_offset`, not from the file
start. This removes a circular dependency: the index content (which contains the
offsets) would otherwise depend on the index's own size.

## Compression identifiers

| id | name | codec |
| ---: | --- | --- |
| 0 | `store` | none |
| 1 | `zlib` | zlib, level 6 |
| 2 | `lzma` | XZ container, preset 6 |

## Hash identifiers

| id | name | digest size |
| ---: | --- | ---: |
| 0 | `none` | 0 |
| 1 | `sha256` | 32 |

## Manifest JSON

Stored as UTF-8 JSON (compressed). Keys:

```jsonc
{
  "format_version": 1,
  "created_by": { "tool": "ECE Universal AutoCompiler", "tool_version": "1.2" },
  "created_utc": "2026-01-01T00:00:00Z",
  "project": {
    "name": "myproject",
    "adapter_id": "python",
    "detected_type": "python"
  },
  "package": {
    "payload_type": "python",
    "entry_points": [],
    "runtime_requirements": []
  },
  "target": {
    "platform": "windows",
    "architecture": "x64",
    "configuration": "release"
  },
  "content": {
    "file_count": 12,
    "total_uncompressed": 34567,
    "hash_algo": "sha256",
    "content_hash": "hex…"
  },
  "signature": null,
  "notes": "Packaged by ECE Universal AutoCompiler"
}
```

Unknown top-level keys from a newer **minor** version are preserved on read and
re-emitted on write (`Manifest.unknown_fields`), so an older tool does not strip
newer metadata.

### `content_hash`

`content_hash` is a deterministic package-level digest. It is computed over the
entries **sorted by path**, hashing for each:

```
path_utf8 0x00 hexdigest_ascii 0x00 size_ascii 0x0A
```

This makes the hash independent of entry order while still covering every
file's identity, content and size.

### `signature` (optional, `null` by default)

Hashes detect **corruption**, not **authorship**: anyone can rebuild a
container and produce fresh, internally consistent hashes. A publisher that
wants authenticity can attach a detached signature:

```jsonc
"signature": { "alg": "minisign", "value": "base64…" }
```

| key | value |
| --- | --- |
| `alg` | `minisign`, `cosign` or `gpg`. |
| `value` | Base64 of the detached signature bytes. |

The signed message is the manifest's `content_hash` (lowercase hex, ASCII) —
so a valid signature covers every file's identity, content and size at once.

Compatibility and behaviour:

* The field is **optional and backward compatible**: containers written by
  older tools have no `signature` key and are read as `null`. Unsigned
  containers (`null`) behave exactly like before.
* Verification (`validate --verify-signature`, `extract --verify-signature`)
  shells out to the matching external tool. Without the flag only hashes are
  checked — by default nothing is verified but integrity.
* If the external tool or the publisher's public key is not on the machine,
  the result is a **warning**, not an error. An **invalid** signature is an
  error and only surfaces with `--verify-signature`.
* Public key discovery: a `C:\...\project.ece.pub` text file next to the
  container, or the `ECE_MINISIGN_PUBKEY` / `ECE_COSIGN_KEY` environment
  variables. `gpg` uses the user's own keyring.

See `container/signature.py` (executable reference) and
[SECURITY.md](SECURITY.md#optional-signatures) for the full flow.

## Index JSON

```jsonc
{
  "entries": [
    {
      "path": "dir/file.txt",
      "offset": 0,            // relative to payload_offset
      "length": 1024,         // stored (possibly compressed) length
      "size": 4096,           // uncompressed size
      "compression": "zlib",
      "hash_algo": "sha256",
      "hash": "hex…",         // digest of the uncompressed bytes
      "executable": false
    }
  ]
}
```

Entries are written sorted by `path`. `path` is always relative and uses `/`;
see `container/security.py` for the rules a path must satisfy.

## Validation levels

- **Structural** (`validate_container(deep=False)`): header fields consistent,
  blocks within bounds, counts match, paths safe, no overlapping/out-of-range
  payload regions.
- **Deep** (`deep=True`): additionally re-hashes every entry and checks the
  package `content_hash`.
- **Signature** (`verify_signature=True`, CLI `--verify-signature`): verifies
  the optional manifest signature with its external tool (minisign/cosign/gpg).

Hashes (either level) prove integrity, not authenticity — see
[SECURITY.md](SECURITY.md).

## Versioning policy

- **Major** bump = incompatible layout. Readers refuse unknown majors.
- **Minor** bump = backward-compatible. Readers ignore unknown trailing header
  bytes and preserve unknown JSON keys.
