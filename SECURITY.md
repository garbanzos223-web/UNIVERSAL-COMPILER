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

The project is at `0.1.x` (alpha). Only the latest release receives fixes.

## Scope

In scope:

- The tool's own code: command execution, path handling, container parsing,
  extraction, secret handling, the GUI/CLI.
- The `.ece` reader/validator/extractor against malicious containers.

Out of scope:

- Vulnerabilities in the software you ask the tool to build.
- Trust decisions about a project's own scripts: the tool asks for consent, but
  once you grant it, those scripts run with your privileges by design.
