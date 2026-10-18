"""Optional manifest signature verification using external tools.

Hashes inside an ``.ece`` container detect corruption, but they are **not** a
publisher signature: anyone can rebuild a container with fresh hashes. A
publisher may therefore attach a detached signature object to the manifest:

    "signature": { "alg": "minisign" | "cosign" | "gpg", "value": "<base64>" }

The signed message is the manifest's ``content_hash`` (lowercase hex, ASCII).
This module verifies that signature by shelling out to the corresponding
external tool (argv lists, never a shell string). The tools are never bundled
and are never required:

  * tool not installed  -> warning + winget/scoop/choco hint (not an error);
  * public key missing  -> warning (nothing proves the container is bad);
  * the tool rejects it -> error.

Public key discovery for minisign/cosign: a ``<container>.pub`` text file next
to the container, or the ``ECE_MINISIGN_PUBKEY`` / ``ECE_COSIGN_KEY``
environment variables. gpg verifies against the user's own keyring and needs
no key file.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..logging_config import get_logger

log = get_logger("container.signature")

SIGNATURE_ALGS: tuple[str, ...] = ("minisign", "cosign", "gpg")

STATE_NO_SIGNATURE = "none"
STATE_VERIFIED = "verified"
STATE_INVALID = "invalid"
STATE_TOOL_MISSING = "tool-missing"
STATE_KEY_MISSING = "key-missing"

TOOL_BY_ALG = {"minisign": "minisign", "cosign": "cosign", "gpg": "gpg"}

# Verified against the real Windows package managers (winget ids confirmed).
INSTALL_HINTS = {
    "minisign": "Install minisign: winget install jedisct1.minisign "
                "(or: scoop install minisign)",
    "cosign": "Install cosign: winget install Sigstore.Cosign "
              "(or: scoop install cosign)",
    "gpg": "Install Gpg4win: winget install GnuPG.Gpg4win "
           "(or: choco install gnupg)",
}

TIMEOUT_SECONDS = 60


@dataclass
class SignatureStatus:
    present: bool = False
    alg: str = ""
    state: str = STATE_NO_SIGNATURE
    detail: str = ""

    @property
    def ok(self) -> bool:
        """True only when a signature was present *and* verified."""
        return self.state == STATE_VERIFIED


def decode_signature_value(value: str) -> bytes:
    """Strict base64 decode; raises ValueError on malformed input."""
    try:
        decoded = base64.b64decode(value, validate=True)
    except Exception as exc:  # binascii.Error and friends
        raise ValueError(f"not valid base64: {exc}") from exc
    if not decoded:
        raise ValueError("signature is empty")
    return decoded


def _read_public_key(container: Path, env_var: str) -> str | None:
    """Public key text from ``<container>.pub`` or an environment variable."""
    key_file = container.with_name(container.name + ".pub")
    if key_file.is_file():
        try:
            text = key_file.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        if text:
            return text
    env = os.environ.get(env_var, "").strip()
    return env or None


def verify_signature(
    manifest: Any,
    container_path: Path | str,
    *,
    timeout: int = TIMEOUT_SECONDS,
) -> SignatureStatus:
    """Verify *manifest*'s optional signature against its external tool.

    Never raises for an unverifiable container: problems come back as a
    ``SignatureStatus`` whose ``state`` says what happened.
    """
    container = Path(container_path)
    sig = getattr(manifest, "signature", None)
    if not sig:
        return SignatureStatus(present=False, state=STATE_NO_SIGNATURE)

    alg = str(sig.get("alg", ""))
    value = str(sig.get("value", ""))
    status = SignatureStatus(present=True, alg=alg)

    tool = TOOL_BY_ALG.get(alg)
    if tool is None:
        status.state = STATE_INVALID
        status.detail = f"unknown signature algorithm: {alg}"
        return status

    try:
        signature_bytes = decode_signature_value(value)
    except ValueError as exc:
        status.state = STATE_INVALID
        status.detail = str(exc)
        return status

    exe = shutil.which(tool)
    if exe is None:
        status.state = STATE_TOOL_MISSING
        status.detail = INSTALL_HINTS[tool]
        return status

    content_hash = str(getattr(manifest, "content_hash", "")).strip()
    if not content_hash:
        status.state = STATE_INVALID
        status.detail = "manifest has no content hash to verify"
        return status

    # Public key (gpg verifies against the local keyring and needs none).
    key_arg: list[str] = []
    temp_key: Path | None = None
    if alg == "minisign":
        key = _read_public_key(container, "ECE_MINISIGN_PUBKEY")
        if key is None:
            status.state = STATE_KEY_MISSING
            status.detail = (
                f"public key not found; put the publisher's key in "
                f"{container.name}.pub next to the container or set "
                f"ECE_MINISIGN_PUBKEY"
            )
            return status
        key_arg = ["-P", key]
    elif alg == "cosign":
        key = _read_public_key(container, "ECE_COSIGN_KEY")
        if key is None:
            status.state = STATE_KEY_MISSING
            status.detail = (
                f"public key not found; put the publisher's key in "
                f"{container.name}.pub next to the container or set "
                f"ECE_COSIGN_KEY"
            )
            return status
        key_path = Path(key).expanduser()
        if key_path.is_file():
            temp_key = None
            key_file = key_path
        else:
            handle, name = tempfile.mkstemp(prefix="ece-cosign-key-", suffix=".pub")
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                fh.write(key)
            temp_key = Path(name)
            key_file = temp_key
        key_arg = ["--key", str(key_file)]

    try:
        with tempfile.TemporaryDirectory(prefix="ece-sig-") as tmp:
            tmpdir = Path(tmp)
            message = tmpdir / "message.txt"
            message.write_text(content_hash, encoding="ascii")
            signature_file = tmpdir / "signature.bin"
            signature_file.write_bytes(signature_bytes)

            if alg == "minisign":
                argv = [exe, "-Vm", str(message), "-x", str(signature_file), *key_arg]
            elif alg == "cosign":
                argv = [exe, "verify-blob", str(message),
                        "--signature", str(signature_file), *key_arg]
            else:  # gpg
                argv = [exe, "--batch", "--status-fd", "1",
                        "--verify", str(signature_file), str(message)]

            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        status.state = STATE_INVALID
        status.detail = f"{tool} did not answer within {timeout}s"
        return status
    except OSError as exc:
        status.state = STATE_TOOL_MISSING
        status.detail = f"{tool} could not be run: {exc}"
        return status
    finally:
        if temp_key is not None:
            try:
                temp_key.unlink(missing_ok=True)
            except OSError:  # pragma: no cover - best effort cleanup
                pass

    if proc.returncode == 0:
        status.state = STATE_VERIFIED
        status.detail = f"verified with {tool}"
        log.info("Signature verified for %s using %s", container, tool)
    else:
        status.state = STATE_INVALID
        tail = [line for line in (proc.stderr or proc.stdout or "").splitlines() if line.strip()]
        status.detail = tail[-1] if tail else f"{tool} rejected the signature (exit {proc.returncode})"
        log.warning("Signature rejected for %s by %s: %s", container, tool, status.detail)
    return status
