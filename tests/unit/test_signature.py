"""Tests for the optional manifest signature (container/signature.py).

Real signature tools (minisign/cosign/gpg) are external and may be absent, so
the plumbing is tested with a fake tool on PATH: a ``<name>.cmd`` script that
exits with a chosen code. That exercises argument construction, key discovery
and result mapping without depending on any installed tool or the network.
"""

import base64
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


def _add_src() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


_add_src()

from ece_autocompiler.container import (  # noqa: E402
    ContainerReader,
    ContainerWriter,
    Manifest,
    PayloadItem,
    validate_container,
)
from ece_autocompiler.container.signature import (  # noqa: E402
    STATE_INVALID,
    STATE_KEY_MISSING,
    STATE_NO_SIGNATURE,
    STATE_TOOL_MISSING,
    STATE_VERIFIED,
    decode_signature_value,
    verify_signature,
)
from ece_autocompiler.errors import ContainerFormatError, IntegrityError  # noqa: E402
from ece_autocompiler.extraction import Extractor  # noqa: E402

SIG_BYTES = b"fake-signature-bytes-for-tests"
SIG_B64 = base64.b64encode(SIG_BYTES).decode("ascii")


class _FakeToolMixin:
    """Install/remove a fake signature tool on PATH for one test."""

    tmp: Path

    def fake_tool(self, name: str, exit_code: int, *, extra_env: dict | None = None) -> None:
        bindir = self.tmp / f"fakebin_{name}_{exit_code}"
        bindir.mkdir(parents=True, exist_ok=True)
        script = bindir / f"{name}.cmd"
        script.write_text(f"@echo off\r\necho fake {name} says {exit_code}\r\nexit /b {exit_code}\r\n",
                          encoding="ascii")
        env = {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}
        if extra_env:
            env.update(extra_env)
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def empty_path(self, **env: str) -> None:
        """PATH with no tool in it: the tool is reported missing."""
        empty = self.tmp / "emptybin"
        empty.mkdir(parents=True, exist_ok=True)
        patcher = mock.patch.dict(os.environ, {"PATH": str(empty), **env})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_signed_container(self, *, signature: dict | None = None) -> Path:
        files = {"a.txt": b"hello world\n" * 4, "dir/b.bin": bytes(range(64))}
        items = []
        for i, (rel, data) in enumerate(files.items()):
            src = self.tmp / f"src_{i}"
            src.write_bytes(data)
            items.append(PayloadItem(path=rel, source=src))
        manifest = Manifest(project_name="signed", adapter_id="python", payload_type="python")
        manifest.signature = signature
        out = self.tmp / "signed.ece"
        ContainerWriter(compression="zlib").write(out, items, manifest, overwrite=True)
        return out


class ManifestSignatureTests(unittest.TestCase):
    def test_unsigned_manifest_emits_null(self) -> None:
        data = Manifest(project_name="p").to_dict()
        self.assertIn("signature", data)
        self.assertIsNone(data["signature"])

    def test_roundtrip_with_signature(self) -> None:
        raw = Manifest(project_name="p").to_dict()
        raw["signature"] = {"alg": "minisign", "value": SIG_B64}
        parsed = Manifest.from_dict(raw)
        self.assertEqual(parsed.signature, {"alg": "minisign", "value": SIG_B64})
        self.assertEqual(parsed.to_dict()["signature"], {"alg": "minisign", "value": SIG_B64})

    def test_old_manifest_without_signature_key(self) -> None:
        raw = Manifest(project_name="p").to_dict()
        del raw["signature"]  # a container written by an older tool
        parsed = Manifest.from_dict(raw)
        self.assertIsNone(parsed.signature)

    def test_null_signature_is_accepted(self) -> None:
        raw = Manifest(project_name="p").to_dict()
        raw["signature"] = None
        self.assertIsNone(Manifest.from_dict(raw).signature)

    def test_rejects_unknown_algorithm(self) -> None:
        raw = Manifest(project_name="p").to_dict()
        raw["signature"] = {"alg": "rot13", "value": SIG_B64}
        with self.assertRaises(ContainerFormatError):
            Manifest.from_dict(raw)

    def test_rejects_invalid_base64(self) -> None:
        raw = Manifest(project_name="p").to_dict()
        raw["signature"] = {"alg": "minisign", "value": "not base64!!"}
        with self.assertRaises(ContainerFormatError):
            Manifest.from_dict(raw)

    def test_rejects_empty_value(self) -> None:
        raw = Manifest(project_name="p").to_dict()
        raw["signature"] = {"alg": "minisign", "value": ""}
        with self.assertRaises(ContainerFormatError):
            Manifest.from_dict(raw)

    def test_rejects_non_object(self) -> None:
        raw = Manifest(project_name="p").to_dict()
        raw["signature"] = "just a string"
        with self.assertRaises(ContainerFormatError):
            Manifest.from_dict(raw)

    def test_decode_rejects_garbage(self) -> None:
        with self.assertRaises(ValueError):
            decode_signature_value("!!!not-base64!!!")
        with self.assertRaises(ValueError):
            decode_signature_value("")


class SignatureVerificationTests(unittest.TestCase, _FakeToolMixin):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ece-sig-test-"))

    def test_unsigned_container_reports_no_signature(self) -> None:
        out = self.write_signed_container(signature=None)
        with ContainerReader(out) as reader:
            status = verify_signature(reader.manifest, out)
        self.assertFalse(status.present)
        self.assertEqual(status.state, STATE_NO_SIGNATURE)

    def test_valid_signature_verifies(self) -> None:
        out = self.write_signed_container(signature={"alg": "minisign", "value": SIG_B64})
        self.fake_tool("minisign", 0, extra_env={"ECE_MINISIGN_PUBKEY": "fakekey"})
        report = validate_container(out, verify_signature=True)
        self.assertTrue(report.ok, [str(i) for i in report.issues])
        self.assertEqual(report.signature_state, STATE_VERIFIED)
        self.assertIn("ece.container.signature_verified", [i.code for i in report.issues])

    def test_invalid_signature_is_an_error(self) -> None:
        out = self.write_signed_container(signature={"alg": "minisign", "value": SIG_B64})
        self.fake_tool("minisign", 1, extra_env={"ECE_MINISIGN_PUBKEY": "fakekey"})
        report = validate_container(out, verify_signature=True)
        self.assertFalse(report.ok)
        self.assertEqual(report.signature_state, STATE_INVALID)
        self.assertIn("ece.container.signature_invalid", [i.code for i in report.errors])

    def test_without_flag_the_signature_is_not_checked(self) -> None:
        out = self.write_signed_container(signature={"alg": "minisign", "value": SIG_B64})
        self.fake_tool("minisign", 1)  # would fail loudly if it were consulted
        report = validate_container(out)
        self.assertTrue(report.ok)
        # The state only records that a signature exists; the tool was not run.
        self.assertEqual(report.signature_state, "present")

    def test_missing_tool_is_a_warning(self) -> None:
        out = self.write_signed_container(signature={"alg": "minisign", "value": SIG_B64})
        self.empty_path(ECE_MINISIGN_PUBKEY="fakekey")
        report = validate_container(out, verify_signature=True)
        self.assertTrue(report.ok, [str(i) for i in report.issues])
        self.assertEqual(report.signature_state, STATE_TOOL_MISSING)
        self.assertIn("ece.container.signature_tool_missing", [i.code for i in report.warnings])

    def test_missing_public_key_is_a_warning(self) -> None:
        out = self.write_signed_container(signature={"alg": "minisign", "value": SIG_B64})
        self.fake_tool("minisign", 0, extra_env={"ECE_MINISIGN_PUBKEY": ""})
        report = validate_container(out, verify_signature=True)
        self.assertTrue(report.ok, [str(i) for i in report.issues])
        self.assertEqual(report.signature_state, STATE_KEY_MISSING)
        self.assertIn("ece.container.signature_key_missing", [i.code for i in report.warnings])

    def test_public_key_file_next_to_container(self) -> None:
        out = self.write_signed_container(signature={"alg": "minisign", "value": SIG_B64})
        out.with_name(out.name + ".pub").write_text("fakekey\n", encoding="utf-8")
        self.fake_tool("minisign", 0)
        status_report = validate_container(out, verify_signature=True)
        self.assertEqual(status_report.signature_state, STATE_VERIFIED)

    def test_cosign_inline_key_uses_temp_key_file(self) -> None:
        out = self.write_signed_container(signature={"alg": "cosign", "value": SIG_B64})
        self.fake_tool("cosign", 0, extra_env={"ECE_COSIGN_KEY": "-----BEGIN PUBLIC KEY-----\nfake\n-----END PUBLIC KEY-----"})
        report = validate_container(out, verify_signature=True)
        self.assertEqual(report.signature_state, STATE_VERIFIED)

    def test_gpg_uses_keyring_and_needs_no_key_file(self) -> None:
        out = self.write_signed_container(signature={"alg": "gpg", "value": SIG_B64})
        self.fake_tool("gpg", 0)
        report = validate_container(out, verify_signature=True)
        self.assertEqual(report.signature_state, STATE_VERIFIED)

    def test_unsigned_container_with_flag_is_fine(self) -> None:
        out = self.write_signed_container(signature=None)
        report = validate_container(out, verify_signature=True)
        self.assertTrue(report.ok)
        self.assertEqual(report.signature_state, STATE_NO_SIGNATURE)
        self.assertIn("ece.container.unsigned", [i.code for i in report.issues])

    def test_signed_container_without_flag_shows_present(self) -> None:
        out = self.write_signed_container(signature={"alg": "minisign", "value": SIG_B64})
        report = validate_container(out)
        self.assertTrue(report.ok)
        self.assertEqual(report.signature_state, "present")


class ExtractSignatureTests(unittest.TestCase, _FakeToolMixin):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ece-sig-extract-"))

    def test_invalid_signature_aborts_extraction(self) -> None:
        out = self.write_signed_container(signature={"alg": "minisign", "value": SIG_B64})
        self.fake_tool("minisign", 1, extra_env={"ECE_MINISIGN_PUBKEY": "fakekey"})
        dest = self.tmp / "out"
        with self.assertRaises(IntegrityError):
            Extractor().extract(out, dest, verify_signature=True)
        self.assertTrue(dest.is_dir())
        self.assertEqual(list(dest.iterdir()), [], "no file may be written on a bad signature")

    def test_valid_signature_extracts(self) -> None:
        out = self.write_signed_container(signature={"alg": "minisign", "value": SIG_B64})
        out.with_name(out.name + ".pub").write_text("fakekey\n", encoding="utf-8")
        self.fake_tool("minisign", 0)
        dest = self.tmp / "out_ok"
        report = Extractor().extract(out, dest, verify_signature=True)
        self.assertEqual(report.files_written, 2)
        self.assertEqual((dest / "a.txt").read_bytes(), b"hello world\n" * 4)

    def test_extraction_without_flag_ignores_a_bad_signature(self) -> None:
        out = self.write_signed_container(signature={"alg": "minisign", "value": SIG_B64})
        self.fake_tool("minisign", 1, extra_env={"ECE_MINISIGN_PUBKEY": "fakekey"})
        dest = self.tmp / "out_noflag"
        report = Extractor().extract(out, dest)
        self.assertEqual(report.files_written, 2)

    def test_missing_tool_warns_but_extracts(self) -> None:
        out = self.write_signed_container(signature={"alg": "minisign", "value": SIG_B64})
        self.empty_path(ECE_MINISIGN_PUBKEY="fakekey")
        dest = self.tmp / "out_warn"
        report = Extractor().extract(out, dest, verify_signature=True)
        self.assertEqual(report.files_written, 2)
        self.assertTrue(report.warnings, "an unverifiable signature must warn")


class CliSignatureTests(unittest.TestCase, _FakeToolMixin):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ece-sig-cli-"))

    def _run_cli(self, argv: list[str]) -> tuple[int, str]:
        from ece_autocompiler import cli

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(argv)
        return code, buf.getvalue()

    def test_validate_json_reports_signature_state(self) -> None:
        out = self.write_signed_container(signature={"alg": "minisign", "value": SIG_B64})
        self.fake_tool("minisign", 0, extra_env={"ECE_MINISIGN_PUBKEY": "fakekey"})
        code, text = self._run_cli(["validate", str(out), "--verify-signature", "--json"])
        self.assertEqual(code, 0, text)
        payload = json.loads(text)
        self.assertEqual(payload["signature_state"], STATE_VERIFIED)
        self.assertTrue(payload["ok"])

    def test_validate_invalid_signature_fails(self) -> None:
        out = self.write_signed_container(signature={"alg": "minisign", "value": SIG_B64})
        self.fake_tool("minisign", 1, extra_env={"ECE_MINISIGN_PUBKEY": "fakekey"})
        code, text = self._run_cli(["validate", str(out), "--verify-signature", "--json"])
        self.assertEqual(code, 1, text)

    def test_extract_with_bad_signature_returns_error(self) -> None:
        out = self.write_signed_container(signature={"alg": "minisign", "value": SIG_B64})
        self.fake_tool("minisign", 1, extra_env={"ECE_MINISIGN_PUBKEY": "fakekey"})
        dest = self.tmp / "cli_out"
        code, text = self._run_cli(["extract", str(out), str(dest), "--verify-signature"])
        self.assertEqual(code, 1, text)
        self.assertIn("signature is invalid", text.lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
