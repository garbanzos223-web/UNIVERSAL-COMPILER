import sys, tempfile, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ece_autocompiler.container import (
    ContainerReader, ContainerWriter, Manifest, PayloadItem, validate_container,
)
from ece_autocompiler.extraction import Extractor

tmp = Path(tempfile.mkdtemp())
out = tmp / "demo.ece"

items = [
    PayloadItem(path="hello.txt", data=b"hello world\n"),
    PayloadItem(path="pkg/app.py", data=b"print('hi')\n"),
    PayloadItem(path="pkg/data/big.bin", data=bytes(range(256)) * 40),
]
man = Manifest(project_name="demo", adapter_id="python", payload_type="python-app",
               platform="windows", architecture="x64", configuration="release")
rep = ContainerWriter(compression="zlib").write(out, items, man)
print("write:", rep.file_count, rep.total_uncompressed, rep.total_compressed, rep.content_hash[:16])

vr = validate_container(out, deep=True)
print("valid:", vr.ok, "entries:", vr.entry_count, "issues:", [str(i) for i in vr.issues])

with ContainerReader(out) as r:
    assert r.read_entry(r.get_entry("hello.txt")) == b"hello world\n"
    assert r.read_entry(r.get_entry("pkg/data/big.bin")) == bytes(range(256)) * 40
    print("manifest:", r.manifest.project_name, r.manifest.payload_type)

dest = tmp / "extract"
er = Extractor().extract(out, dest, overwrite=True)
print("extract:", er.summary)
assert (dest / "hello.txt").read_bytes() == b"hello world\n"
assert (dest / "pkg" / "app.py").read_bytes() == b"print('hi')\n"
print("ROUNDTRIP OK")

# lzma + store
for comp in ("store", "lzma"):
    o2 = tmp / f"demo-{comp}.ece"
    ContainerWriter(compression=comp).write(o2, items, Manifest(project_name="demo"))
    with ContainerReader(o2) as r:
        assert r.read_entry(r.get_entry("pkg/data/big.bin")) == bytes(range(256)) * 40
    print(f"{comp} OK")

# corruption: flip a byte and confirm both the affected entry read and a deep
# validation reject the file.
data = bytearray(out.read_bytes())
data[-10] ^= 0xFF
bad = tmp / "bad.ece"
bad.write_bytes(data)

from ece_autocompiler.errors import ContainerError

detected = False
try:
    with ContainerReader(bad) as r:
        r.read_entry(r.get_entry("pkg/data/big.bin"))
except ContainerError as e:
    detected = True
    print("corruption detected on read:", e.code)

if not validate_container(bad, deep=True).ok:
    detected = True
    print("corruption detected on deep validation")

# truncation must also be rejected
trunc = tmp / "trunc.ece"
trunc.write_bytes(out.read_bytes()[:-40])
if not validate_container(trunc, deep=True).ok:
    detected = True
    print("truncation detected")

print("CORRUPTION CHECKS OK" if detected else "corruption NOT detected (unexpected)")
