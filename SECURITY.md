# Security policy

## Reporting a vulnerability

Do not open a public issue for a security problem. Report it privately to the
project maintainers (see the repository's Security tab, or the contact in the
project metadata). Include:

- what you affected (the tool, an adapter, or the `.ece` format),
- a minimal reproduction,
- the impact you believe it has.

We aim to acknowledge reports within a few days.

## Supported versions

The current application release is `1.3.x`. Only the latest release receives
fixes. The `.ece` container format is version 1 and remains readable across
application releases.

## Scope

In scope:

- The tool's own code: command execution, path handling, container parsing,
  extraction, secret handling, the GUI/CLI.
- The `.ece` reader/validator/extractor against malicious containers.
- The decompiler readers and backends against hostile input: PE, ELF, .NET
  metadata, Java class files, Python bytecode, PyInstaller CArchive/PYZ,
  archives and ECE payloads.
- The recompiler's command construction, consent gates and output verification.

Out of scope:

- Vulnerabilities in the software you ask the tool to build or decompile.
- Trust decisions about a project's own scripts: the tool asks for consent, but
  once you grant it, those scripts run with your privileges by design.
- The optional external tools (ILSpy, JDK, compilers). Report those to their
  vendors; we only select the command and pass argv lists.
