"""Java class-file reader and reconstructed-class outline generator.

The constant pool, access flags, fields, methods and the SourceFile attribute
are parsed directly, so this works with a JRE-only machine. When a JDK with
``javap`` is available the Java backend also produces real decompiled Java and
bytecode listings; otherwise it produces an outline that is clearly marked and
never presented as original source.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONSTANT_NAMES = {
    1: "Utf8", 3: "Integer", 4: "Float", 5: "Long", 6: "Double", 7: "Class",
    8: "String", 9: "Fieldref", 10: "Methodref", 11: "InterfaceMethodref",
    12: "NameAndType", 15: "MethodHandle", 16: "MethodType", 17: "Dynamic",
    18: "InvokeDynamic", 19: "Module", 20: "Package",
}

ACC_PUBLIC = 0x0001
ACC_PRIVATE = 0x0002
ACC_PROTECTED = 0x0004
ACC_STATIC = 0x0008
ACC_FINAL = 0x0010
ACC_SUPER = 0x0020
ACC_SYNCHRONIZED = 0x0020
ACC_VOLATILE = 0x0040
ACC_BRIDGE = 0x0040
ACC_TRANSIENT = 0x0080
ACC_VARARGS = 0x0080
ACC_NATIVE = 0x0100
ACC_INTERFACE = 0x0200
ACC_ABSTRACT = 0x0400
ACC_STRICT = 0x0800
ACC_SYNTHETIC = 0x1000
ACC_ANNOTATION = 0x2000
ACC_ENUM = 0x4000
ACC_MODULE = 0x8000
MAX_CLASS_BYTES = 64 * 1024 * 1024


@dataclass
class FieldInfo:
    access: int
    name: str
    descriptor: str

    def to_dict(self) -> dict[str, Any]:
        return {"access": self.access, "name": self.name, "descriptor": self.descriptor}


@dataclass
class MethodInfo:
    access: int
    name: str
    descriptor: str
    code_length: int = 0
    max_stack: int = 0
    max_locals: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "access": self.access,
            "name": self.name,
            "descriptor": self.descriptor,
            "code_length": self.code_length,
            "max_stack": self.max_stack,
            "max_locals": self.max_locals,
        }


@dataclass
class ClassModel:
    path: Path
    major: int
    minor: int
    access: int
    this_class: str
    super_class: str
    interfaces: list[str] = field(default_factory=list)
    fields: list[FieldInfo] = field(default_factory=list)
    methods: list[MethodInfo] = field(default_factory=list)
    source_file: str = ""
    constant_pool_size: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def package(self) -> str:
        return self.this_class.rsplit(".", 1)[0] if "." in self.this_class else ""

    @property
    def simple_name(self) -> str:
        return self.this_class.rsplit(".", 1)[-1]

    @property
    def is_interface(self) -> bool:
        return bool(self.access & ACC_INTERFACE)

    @property
    def is_enum(self) -> bool:
        return bool(self.access & ACC_ENUM)

    @property
    def is_annotation(self) -> bool:
        return bool(self.access & ACC_ANNOTATION)

    def to_dict(self) -> dict[str, Any]:
        return {
            "class": self.this_class,
            "super": self.super_class,
            "interfaces": self.interfaces,
            "class_file_version": f"{self.major}.{self.minor}",
            "access": self.access,
            "source_file": self.source_file,
            "constant_pool": self.constant_pool_size,
            "fields": [f.to_dict() for f in self.fields],
            "methods": [m.to_dict() for m in self.methods],
            "warnings": self.warnings,
        }


def _modified_utf8(blob: bytes) -> str:
    # Class files use modified UTF-8; ``replace`` keeps hostile input readable.
    return blob.decode("utf-8", errors="replace")


def parse_class(path: Path) -> ClassModel:
    path = Path(path)
    if path.stat().st_size > MAX_CLASS_BYTES:
        raise ValueError("the class file is too large for the bundled reader")
    data = path.read_bytes()
    if len(data) < 10 or data[:4] != b"\xca\xfe\xba\xbe":
        raise ValueError("not a Java class file (missing CAFEBABE)")
    minor, major = struct.unpack_from(">HH", data, 4)
    at = 8
    count = struct.unpack_from(">H", data, at)[0]
    at += 2
    pool: list[Any] = [None] * count
    index = 1
    while index < count:
        if at >= len(data):
            raise ValueError("truncated constant pool")
        tag = data[at]
        at += 1
        if tag == 1:
            length = struct.unpack_from(">H", data, at)[0]
            at += 2
            pool[index] = _modified_utf8(data[at : at + length])
            at += length
        elif tag in (3, 4):
            pool[index] = struct.unpack_from(">i", data, at)[0]
            at += 4
        elif tag in (5, 6):
            pool[index] = struct.unpack_from(">q", data, at)[0]
            at += 8
            index += 1
        elif tag in (7, 8, 16, 19, 20):
            pool[index] = struct.unpack_from(">H", data, at)[0]
            at += 2
        elif tag in (9, 10, 11, 12, 17, 18):
            pool[index] = struct.unpack_from(">HH", data, at)
            at += 4
        elif tag == 15:
            pool[index] = (data[at], struct.unpack_from(">H", data, at + 1)[0])
            at += 3
        else:
            raise ValueError(f"unknown constant pool tag {tag} at index {index}")
        index += 1

    def utf8(value_index: int) -> str:
        if 0 < value_index < len(pool) and isinstance(pool[value_index], str):
            return pool[value_index]
        return ""

    def class_name(value_index: int) -> str:
        if 0 < value_index < len(pool) and isinstance(pool[value_index], int):
            return utf8(pool[value_index]).replace("/", ".")
        return ""

    if at + 8 > len(data):
        raise ValueError("truncated class declaration")
    access, this_class, super_class, interfaces_count = struct.unpack_from(">HHHH", data, at)
    at += 8
    interfaces: list[str] = []
    for _ in range(min(interfaces_count, 2000)):
        if at + 2 > len(data):
            break
        interfaces.append(class_name(struct.unpack_from(">H", data, at)[0]))
        at += 2

    def read_attributes(cursor: int) -> tuple[list[tuple[str, bytes]], int]:
        attributes: list[tuple[str, bytes]] = []
        if cursor + 2 > len(data):
            return attributes, cursor
        attribute_count = struct.unpack_from(">H", data, cursor)[0]
        cursor += 2
        for _ in range(min(attribute_count, 2000)):
            if cursor + 6 > len(data):
                break
            name_index, length = struct.unpack_from(">HI", data, cursor)
            cursor += 6
            blob = data[cursor : cursor + length]
            attributes.append((utf8(name_index), blob))
            cursor += length
        return attributes, cursor

    fields: list[FieldInfo] = []
    if at + 2 <= len(data):
        field_count = struct.unpack_from(">H", data, at)[0]
        at += 2
        for _ in range(min(field_count, 5000)):
            if at + 6 > len(data):
                break
            f_access, name_index, desc_index = struct.unpack_from(">HHH", data, at)
            at += 6
            _attributes, at = read_attributes(at)
            fields.append(FieldInfo(f_access, utf8(name_index), utf8(desc_index)))

    methods: list[MethodInfo] = []
    if at + 2 <= len(data):
        method_count = struct.unpack_from(">H", data, at)[0]
        at += 2
        for _ in range(min(method_count, 20000)):
            if at + 6 > len(data):
                break
            m_access, name_index, desc_index = struct.unpack_from(">HHH", data, at)
            at += 6
            attributes, at = read_attributes(at)
            method = MethodInfo(m_access, utf8(name_index), utf8(desc_index))
            for attribute_name, blob in attributes:
                if attribute_name == "Code" and len(blob) >= 8:
                    method.max_stack, method.max_locals = struct.unpack_from(">HH", blob, 0)
                    method.code_length = struct.unpack_from(">I", blob, 4)[0]
            methods.append(method)

    source_file = ""
    class_attributes, at = read_attributes(at)
    for attribute_name, blob in class_attributes:
        if attribute_name == "SourceFile" and len(blob) >= 2:
            source_file = utf8(struct.unpack_from(">H", blob, 0)[0])

    return ClassModel(
        path=path,
        major=major,
        minor=minor,
        access=access,
        this_class=class_name(this_class),
        super_class=class_name(super_class),
        interfaces=interfaces,
        fields=fields,
        methods=methods,
        source_file=source_file,
        constant_pool_size=count,
    )


_DESCRIPTOR_PRIMITIVES = {
    "B": "byte", "C": "char", "D": "double", "F": "float", "I": "int",
    "J": "long", "S": "short", "Z": "boolean", "V": "void",
}


def descriptor_to_java(descriptor: str) -> tuple[str, str]:
    """Return (type, remaining descriptor) for one field type descriptor."""
    if not descriptor:
        return "Object", ""
    head = descriptor[0]
    if head in _DESCRIPTOR_PRIMITIVES:
        return _DESCRIPTOR_PRIMITIVES[head], descriptor[1:]
    if head == "L":
        end = descriptor.find(";")
        if end < 0:
            return "Object", ""
        name = descriptor[1:end].replace("/", ".")
        return name, descriptor[end + 1 :]
    if head == "[":
        inner, rest = descriptor_to_java(descriptor[1:])
        return f"{inner}[]", rest
    return "Object", descriptor[1:]


def method_signature(descriptor: str) -> tuple[list[str], str]:
    if not descriptor.startswith("("):
        return [], "void"
    at = 1
    params: list[str] = []
    while at < len(descriptor) and descriptor[at] != ")":
        param, rest = descriptor_to_java(descriptor[at:])
        params.append(param)
        if len(rest) >= len(descriptor[at:]):
            break
        at = len(descriptor) - len(rest)
    return params, descriptor_to_java(descriptor[at + 1 :] if at + 1 <= len(descriptor) else "")[0]


def _modifiers(access: int, *, for_method: bool = False, interface: bool = False) -> str:
    parts: list[str] = []
    if access & ACC_PUBLIC:
        parts.append("public")
    elif access & ACC_PROTECTED:
        parts.append("protected")
    elif access & ACC_PRIVATE:
        parts.append("private")
    if access & ACC_STATIC:
        parts.append("static")
    if access & ACC_FINAL:
        parts.append("final")
    if access & ACC_ABSTRACT or interface:
        parts.append("abstract")
    if access & ACC_NATIVE:
        parts.append("native")
    if for_method and access & ACC_SYNCHRONIZED:
        parts.append("synchronized")
    return " ".join(parts)


def generate_outline(model: ClassModel, *, tool_note: str) -> str:
    """A named class outline: real names/descriptors, generated bodies."""
    lines = [
        "// ---------------------------------------------------------------------------",
        "// Recovered class outline generated by ECE Universal AutoCompiler.",
        "// Names, fields, methods and descriptors come from the class file; method",
        "// bodies are NOT original source. Compile it only as a starting point.",
        f"// {tool_note}",
        "// ---------------------------------------------------------------------------",
    ]
    if model.package:
        lines.append(f"package {model.package};")
        lines.append("")
    kind = "interface" if model.is_interface else ("enum" if model.is_enum else "class")
    modifiers = _modifiers(model.access, interface=model.is_interface and not model.is_annotation)
    declaration = f"{modifiers + ' ' if modifiers else ''}{kind} {model.simple_name}"
    if model.super_class and model.super_class != "java.lang.Object" and not model.is_interface:
        declaration += f" extends {model.super_class}"
    if model.interfaces:
        declaration += " implements " + ", ".join(model.interfaces)
    lines.append(declaration + " {")
    for item in model.fields:
        if item.name.startswith("this$"):
            continue
        field_type, _ = descriptor_to_java(item.descriptor)
        lines.append(f"    {_modifiers(item.access)} {field_type} {item.name};".replace("  ", " "))
    if model.fields:
        lines.append("")
    constructors = [m for m in model.methods if m.name == "<init>"]
    for method in model.methods:
        if method.name.startswith("<"):
            continue
        params, returns = method_signature(method.descriptor)
        params_text = ", ".join(f"{ptype} arg{i}" for i, ptype in enumerate(params))
        mods = _modifiers(method.access)
        lines.append(
            f"    {mods + ' ' if mods else ''}{returns} {method.name}({params_text}) {{"
        )
        lines.append(
            f'        throw new UnsupportedOperationException("recovered outline: '
            f"{model.simple_name}.{method.name}\");"
        )
        lines.append("    }")
    if not model.methods or not [m for m in model.methods if not m.name.startswith("<")]:
        if not constructors:
            lines.append(f"    public {model.simple_name}() {{ }}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def class_summary(path: Path) -> dict[str, Any]:
    model = parse_class(path)
    return model.to_dict()


def write_model(model: ClassModel, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(model.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return destination
