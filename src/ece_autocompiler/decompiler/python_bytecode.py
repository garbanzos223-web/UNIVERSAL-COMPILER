"""CPython bytecode reader and structural source reconstruction.

CPython 3.12 cannot be decompiled to exact source by a bundled pure-Python
decompiler, so this module does two honest things:

1. It reads the real code object with ``marshal`` and reconstructs the module
   structure (imports, assignments, functions, classes, methods and
   signatures) from the actual bytecode.
2. It emits a complete ``dis`` listing, which is the exact instruction stream
   the interpreter runs.

The generated ``.py`` file says at the top that method bodies were omitted; it
is never labelled "recovered source".
"""

from __future__ import annotations

import dis
import importlib.util
import io
import marshal
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import CodeType
from typing import Any

from .formats import PYC_MAGICS

MAX_INSTRUCTIONS = 400000
MAX_MODULES = 5000
MAX_PYC_BYTES = 256 * 1024 * 1024

_HEADER_SIZES = {
    "3.7": 16, "3.8": 16, "3.9": 16, "3.10": 16, "3.11": 16, "3.12": 16, "3.13": 16,
    "3.6": 12,
}


@dataclass
class BytecodeModule:
    path: Path
    magic: bytes
    python_version: str
    header_size: int
    flags: int = 0
    code: CodeType | None = None
    code_name: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def loadable_here(self) -> bool:
        return self.magic == importlib.util.MAGIC_NUMBER

    def to_dict(self) -> dict[str, Any]:
        return {
            "python_version": self.python_version,
            "magic": self.magic.hex(),
            "header_size": self.header_size,
            "flags": self.flags,
            "code_name": self.code_name,
            "loadable_by_this_interpreter": self.loadable_here,
            "warnings": self.warnings,
        }


def read_module(path: Path) -> BytecodeModule:
    path = Path(path)
    if path.stat().st_size > MAX_PYC_BYTES:
        raise ValueError("the bytecode file is too large for the bundled reader")
    data = path.read_bytes()
    if len(data) < 16:
        raise ValueError("file is too short to be Python bytecode")
    magic = data[:2]
    version = PYC_MAGICS.get(magic)
    if version is None:
        raise ValueError(f"unknown Python bytecode magic {magic.hex()}")
    header_size = _HEADER_SIZES.get(version, 16)
    module = BytecodeModule(path=path, magic=data[:4], python_version=version, header_size=header_size)
    if len(data) < header_size:
        module.warnings.append("The pyc header is truncated.")
        return module
    if header_size == 16:
        module.flags = struct.unpack_from("<I", data, 4)[0]
    try:
        code = marshal.loads(data[header_size:])
    except Exception as exc:
        module.warnings.append(f"The code object could not be read: {exc}")
        return module
    if not isinstance(code, CodeType):
        module.warnings.append("The payload is not a code object.")
        return module
    module.code = code
    module.code_name = getattr(code, "co_name", "")
    return module


def module_from_bytes(data: bytes, *, name: str = "<frozen>") -> BytecodeModule:
    """Read a frozen module payload (PyInstaller entry) without a filename."""
    module = BytecodeModule(
        path=Path(name), magic=data[:4], python_version=PYC_MAGICS.get(data[:2], "unknown"), header_size=0
    )
    # PyInstaller stores either a pyc (with header) or a raw marshalled object.
    payload = data
    if data[:2] in PYC_MAGICS and data[2:4] == b"\r\n":
        header_size = _HEADER_SIZES.get(PYC_MAGICS[data[:2]], 16)
        module.header_size = header_size
        payload = data[header_size:]
    try:
        code = marshal.loads(payload)
    except Exception as exc:
        module.warnings.append(f"The frozen module could not be read: {exc}")
        return module
    if isinstance(code, CodeType):
        module.code = code
        module.code_name = getattr(code, "co_name", "")
    else:
        module.warnings.append("The frozen payload is not a code object.")
    return module


def module_from_code(code: CodeType, *, name: str = "<frozen>") -> BytecodeModule:
    """Wrap an already-unmarshalled code object (for example from a PYZ)."""
    module = BytecodeModule(
        path=Path(name),
        magic=importlib.util.MAGIC_NUMBER,
        python_version=python_runtime_version().rsplit(".", 1)[0],
        header_size=0,
        code=code,
        code_name=getattr(code, "co_name", ""),
    )
    return module


def _safe_instructions(code: CodeType) -> list[dis.Instruction]:
    try:
        return list(dis.get_instructions(code))[:MAX_INSTRUCTIONS]
    except Exception:
        return []


def _import_lines(code: CodeType) -> list[str]:
    """Reconstruct import statements from the module's own instructions."""
    instructions = _safe_instructions(code)
    lines: list[str] = []
    for index, instruction in enumerate(instructions):
        if instruction.opname != "IMPORT_NAME":
            continue
        module_name = str(instruction.argval or "")
        follow = instructions[index + 1 : index + 6]
        from_names: list[str] = []
        for candidate in follow:
            if candidate.opname == "IMPORT_FROM":
                from_names.append(str(candidate.argval or ""))
            elif candidate.opname == "STORE_NAME":
                if from_names:
                    lines.append(f"from {module_name} import {', '.join(from_names)}")
                else:
                    alias = candidate.argval
                    lines.append(
                        f"import {module_name}" if alias == module_name else f"import {module_name} as {alias}"
                    )
                break
        else:
            if module_name:
                lines.append(f"import {module_name}")
    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for line in lines:
        if line not in seen:
            seen.add(line)
            unique.append(line)
    return unique[:200]


def _module_assignments(code: CodeType) -> list[str]:
    """Simple module-level ``NAME = literal`` assignments visible in bytecode."""
    instructions = _safe_instructions(code)
    lines: list[str] = []
    pending: object = object()
    missing = object()
    for instruction in instructions:
        if instruction.opname in ("LOAD_CONST",):
            pending = instruction.argval
        elif instruction.opname in ("STORE_NAME", "STORE_GLOBAL"):
            name = str(instruction.argval or "")
            if (
                pending is not missing
                and isinstance(pending, (str, int, float, bool, type(None), bytes, tuple))
                and not name.startswith("__")
                and name not in ("annotations",)
            ):
                try:
                    rendered = repr(pending)
                except Exception:
                    rendered = "..."
                if len(rendered) <= 300 and rendered != repr(name):
                    lines.append(f"{name} = {rendered}")
            pending = missing
        elif instruction.opname in ("POP_TOP", "RETURN_VALUE", "IMPORT_NAME", "IMPORT_FROM"):
            pending = missing
    return lines[:200]


def _arg_signature(code: CodeType) -> str:
    varnames = list(code.co_varnames)
    positional = (getattr(code, "co_posonlyargcount", 0) or 0) + code.co_argcount
    params: list[str] = []
    for index in range(min(positional, len(varnames))):
        params.append(varnames[index])
    if code.co_kwonlyargcount:
        params.append("*")
        start = positional
        for index in range(start, min(start + code.co_kwonlyargcount, len(varnames))):
            params.append(varnames[index])
    if code.co_flags & 0x04:  # CO_VARARGS
        index = positional + code.co_kwonlyargcount
        if index < len(varnames):
            params.append("*" + varnames[index])
    if code.co_flags & 0x08:  # CO_VARKEYWORDS
        index = positional + code.co_kwonlyargcount + (1 if code.co_flags & 0x04 else 0)
        if index < len(varnames):
            params.append("**" + varnames[index])
    return ", ".join(params)


def _is_class_body(code: CodeType) -> bool:
    name = getattr(code, "co_name", "")
    flags = code.co_flags
    return bool(name) and not name.startswith("<") and not (flags & 0x01) and bool(flags & 0x02)


def _collect(code: CodeType, *, prefix: str = "") -> dict[str, list[CodeType]]:
    """Group nested code objects by qualname (functions, classes and methods)."""
    groups: dict[str, list[CodeType]] = {}
    for const in code.co_consts:
        if not isinstance(const, CodeType):
            continue
        qualname = getattr(const, "co_qualname", f"{prefix}{const.co_name}")
        if qualname.startswith("<"):
            continue
        groups.setdefault(qualname, []).append(const)
        for nested, objects in _collect(const, prefix=f"{qualname}.").items():
            groups.setdefault(nested, []).extend(objects)
    return groups


def recover_outline(module: BytecodeModule, *, source_name: str = "") -> str:
    """Generate a structural Python module from the real code objects."""
    code = module.code
    header = [
        "# ---------------------------------------------------------------------------",
        "# Structural reconstruction generated by ECE Universal AutoCompiler.",
        f"# Source bytecode: {source_name or module.path.name}",
        f"# CPython version: {module.python_version}",
        "#",
        "# Imports, constants, function/class names and signatures are recovered from",
        "# the real code objects. Function bodies are NOT original source; they are",
        "# omitted on purpose. This file is not claimed to be runnable.",
        "# ---------------------------------------------------------------------------",
        "",
    ]
    if code is None:
        header.append("# The code object could not be read; only the header is available.")
        return "\n".join(header) + "\n"

    lines = list(header)
    imports = _import_lines(code)
    if imports:
        lines.extend(imports)
        lines.append("")
    assignments = _module_assignments(code)
    if assignments:
        lines.extend(assignments)
        lines.append("")

    groups = _collect(code)
    classes: dict[str, list[CodeType]] = {}
    functions: dict[str, list[CodeType]] = {}
    for qualname, code_objects in groups.items():
        if "." not in qualname:
            functions[qualname] = code_objects
            continue
        class_name, method_name = qualname.rsplit(".", 1)
        classes.setdefault(class_name, [])
        classes[class_name].append(code_objects[0])

    emitted_classes: set[str] = set()
    for class_name, methods in sorted(classes.items()):
        lines.append(f"class {class_name.split('.')[-1]}:")
        lines.append('    """Class reconstructed from the bytecode; bodies omitted."""')
        lines.append("")
        emitted_any = False
        for method in sorted(methods, key=lambda c: c.co_name):
            if method.co_name.startswith("<"):
                continue
            lines.append(f"    def {method.co_name}({_arg_signature(method)}):")
            lines.append("        raise NotImplementedError('recovered outline')")
            lines.append("")
            emitted_any = True
        if not emitted_any:
            lines.append("    pass")
            lines.append("")
        emitted_classes.add(class_name)

    for func_name, code_objects in sorted(functions.items()):
        if func_name.startswith("<"):
            continue
        function = code_objects[0]
        if function.co_name in emitted_classes:
            continue
        lines.append(f"def {function.co_name}({_arg_signature(function)}):")
        lines.append("    raise NotImplementedError('recovered outline')")
        lines.append("")

    if not any(line.startswith(("class ", "def ", "import ", "from ")) for line in lines):
        lines.append("# No module-level definitions were found.")
    return "\n".join(lines) + "\n"


def disassembly(module: BytecodeModule, *, limit: int = MAX_INSTRUCTIONS) -> str:
    if module.code is None:
        return "No code object could be read.\n"
    buffer = io.StringIO()
    buffer.write(f"# Bytecode disassembly: {module.path}\n")
    buffer.write(f"# CPython {module.python_version}\n\n")
    try:
        text = dis.Bytecode(module.code).dis()
        lines = text.splitlines()
        buffer.write("\n".join(lines[:limit]))
    except Exception as exc:  # pragma: no cover - exotic code object
        buffer.write(f"# dis failed: {exc}\n")
    return buffer.getvalue() + "\n"


def code_tree(code: CodeType, *, depth: int = 0, max_depth: int = 4) -> list[dict[str, Any]]:
    if code is None or depth > max_depth:
        return []
    rows: list[dict[str, Any]] = [
        {
            "name": code.co_name,
            "qualname": getattr(code, "co_qualname", code.co_name),
            "firstlineno": code.co_firstlineno,
            "args": code.co_argcount,
            "kwonly": code.co_kwonlyargcount,
            "locals": list(code.co_varnames),
            "names": list(code.co_names)[:100],
        }
    ]
    for const in code.co_consts:
        if isinstance(const, CodeType):
            rows.extend(code_tree(const, depth=depth + 1, max_depth=max_depth))
    return rows


def current_magic() -> bytes:
    return importlib.util.MAGIC_NUMBER


def summary(module: BytecodeModule) -> dict[str, Any]:
    data = module.to_dict()
    if module.code is not None:
        data["module_names"] = list(module.code.co_names)[:200]
        data["module_constants"] = [
            repr(item) for item in module.code.co_consts[:50] if not isinstance(item, CodeType)
        ]
        data["code_objects"] = code_tree(module.code)[:200]
    return data


def python_runtime_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
