import sys, tempfile, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ece_autocompiler.build import BuildOrchestrator
from ece_autocompiler.container import ContainerReader, validate_container
from ece_autocompiler.extraction import Extractor

tmp = Path(tempfile.mkdtemp())
proj = tmp / "myproj"
proj.mkdir()
(proj / "app.py").write_text("def add(a, b):\n    return a + b\n\nif __name__ == '__main__':\n    print(add(1, 2))\n")
(proj / "requirements.txt").write_text("flask\n")
(proj / "pkg").mkdir()
(proj / "pkg" / "util.py").write_text("VALUE = 42\n")

out = tmp / "out" / "myproj.ece"

messages = []
big = BuildOrchestrator()
res, pkg = big.build_and_package(
    proj, out,
    consent_granted=True,
    overwrite=True,
    progress=lambda m, d, t: messages.append(m),
)
print("build ok:", res.ok, "exit:", res.exit_code)
print("pkg ok:", pkg.ok, pkg.message, pkg.container_path)
print("progress sample:", messages[:3], "...", messages[-1])

vr = validate_container(out, deep=True)
print("validate:", vr.ok, "entries:", vr.entry_count, "errors:", [str(i) for i in vr.errors])

dest = tmp / "extract"
Extractor().extract(out, dest, overwrite=True)
print("extracted app.py:", (dest / "app.py").exists())
print("extracted pkg/util.py:", (dest / "pkg" / "util.py").exists())
with ContainerReader(out) as r:
    print("manifest:", json.dumps(r.manifest.to_dict()["project"], indent=0))
print("E2E OK")
