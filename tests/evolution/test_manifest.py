"""Tests for community manifest Ed25519 signing and verification."""

import base64
import pytest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding, PrivateFormat, NoEncryption,
)

from agos.evolution.manifest import (
    hash_file,
    generate_manifest,
    sign_manifest,
    verify_manifest,
    verify_community_integrity,
    file_in_manifest,
    _parse_manifest,
)


def _generate_test_keypair() -> tuple[str, str]:
    """Generate a fresh Ed25519 keypair for testing.

    Returns (private_key_pem, public_key_b64).
    """
    private_key = Ed25519PrivateKey.generate()
    pub_raw = private_key.public_key().public_bytes(
        encoding=Encoding.Raw,
        format=__import__("cryptography.hazmat.primitives.serialization", fromlist=["PublicFormat"]).PublicFormat.Raw,
    )
    pub_b64 = base64.b64encode(pub_raw).decode()
    priv_pem = private_key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    ).decode()
    return priv_pem, pub_b64


@pytest.fixture
def keypair():
    return _generate_test_keypair()


class TestHashFile:
    def test_deterministic(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("hello world", encoding="utf-8")
        assert hash_file(f) == hash_file(f)

    def test_different_content_different_hash(self, tmp_path):
        a = tmp_path / "a.py"
        b = tmp_path / "b.py"
        a.write_text("hello", encoding="utf-8")
        b.write_text("world", encoding="utf-8")
        assert hash_file(a) != hash_file(b)


class TestGenerateManifest:
    def test_lists_all_files(self, tmp_path):
        (tmp_path / "README.md").write_text("# Hello", encoding="utf-8")
        sub = tmp_path / "evolved" / "node1"
        sub.mkdir(parents=True)
        (sub / "tool.py").write_text("def run(): pass", encoding="utf-8")

        manifest = generate_manifest(tmp_path)
        assert "README.md" in manifest
        assert "evolved/node1/tool.py" in manifest

    def test_excludes_manifest_itself(self, tmp_path):
        (tmp_path / "README.md").write_text("# Hi", encoding="utf-8")
        (tmp_path / "MANIFEST.sha256").write_text("old manifest", encoding="utf-8")

        manifest = generate_manifest(tmp_path)
        assert "MANIFEST.sha256" not in manifest

    def test_excludes_gitkeep(self, tmp_path):
        sub = tmp_path / "evolved"
        sub.mkdir()
        (sub / ".gitkeep").write_text("", encoding="utf-8")
        (sub / "real.py").write_text("code", encoding="utf-8")

        manifest = generate_manifest(tmp_path)
        assert ".gitkeep" not in manifest
        assert "real.py" in manifest

    def test_empty_dir(self, tmp_path):
        manifest = generate_manifest(tmp_path)
        assert manifest == ""

    def test_sorted_output(self, tmp_path):
        (tmp_path / "z.py").write_text("z", encoding="utf-8")
        (tmp_path / "a.py").write_text("a", encoding="utf-8")
        (tmp_path / "m.py").write_text("m", encoding="utf-8")

        manifest = generate_manifest(tmp_path)
        lines = manifest.strip().split("\n")
        paths = [l.split("  ", 1)[1] for l in lines]
        assert paths == ["a.py", "m.py", "z.py"]


class TestSignAndParse:
    def test_sign_appends_signature(self, keypair):
        priv_pem, _ = keypair
        manifest = "abc123  README.md"
        signed = sign_manifest(manifest, priv_pem)
        assert "# SIGNATURE:" in signed
        assert manifest in signed

    def test_sign_requires_private_key(self):
        with pytest.raises(ValueError, match="Private key required"):
            sign_manifest("test", None)

    def test_parse_extracts_signature(self):
        signed = "abc123  README.md\n# SIGNATURE: dGVzdHNpZw=="
        body, sig, hashes = _parse_manifest(signed)
        assert sig == "dGVzdHNpZw=="
        assert "README.md" in hashes
        assert hashes["README.md"] == "abc123"

    def test_roundtrip(self, keypair):
        priv_pem, _ = keypair
        manifest = "abc123  file1.py\ndef456  file2.py"
        signed = sign_manifest(manifest, priv_pem)
        body, sig, hashes = _parse_manifest(signed)
        assert body == manifest
        assert len(hashes) == 2
        assert sig != ""


class TestVerifyManifest:
    def test_valid_manifest(self, tmp_path, keypair):
        """Full roundtrip: generate, sign, write, verify."""
        priv_pem, pub_b64 = keypair
        (tmp_path / "README.md").write_text("# Test", encoding="utf-8")
        sub = tmp_path / "evolved" / "n1"
        sub.mkdir(parents=True)
        (sub / "tool.py").write_text("def run(): pass", encoding="utf-8")

        manifest = generate_manifest(tmp_path)
        signed = sign_manifest(manifest, priv_pem)
        (tmp_path / "MANIFEST.sha256").write_text(signed, encoding="utf-8")

        ok, issues = verify_manifest(
            manifest_path=tmp_path / "MANIFEST.sha256",
            public_key_b64=pub_b64,
            community_dir=tmp_path,
        )
        assert ok, f"Should pass: {issues}"
        assert issues == []

    def test_tampered_file(self, tmp_path, keypair):
        """Changing a file after signing should fail verification."""
        priv_pem, pub_b64 = keypair
        (tmp_path / "README.md").write_text("# Original", encoding="utf-8")

        manifest = generate_manifest(tmp_path)
        signed = sign_manifest(manifest, priv_pem)
        (tmp_path / "MANIFEST.sha256").write_text(signed, encoding="utf-8")

        # Tamper
        (tmp_path / "README.md").write_text("# HACKED", encoding="utf-8")

        ok, issues = verify_manifest(
            manifest_path=tmp_path / "MANIFEST.sha256",
            public_key_b64=pub_b64,
            community_dir=tmp_path,
        )
        assert not ok
        assert any("Hash mismatch" in i for i in issues)

    def test_injected_file(self, tmp_path, keypair):
        """Adding a file not in manifest should fail verification."""
        priv_pem, pub_b64 = keypair
        (tmp_path / "README.md").write_text("# Test", encoding="utf-8")

        manifest = generate_manifest(tmp_path)
        signed = sign_manifest(manifest, priv_pem)
        (tmp_path / "MANIFEST.sha256").write_text(signed, encoding="utf-8")

        # Inject
        evil_dir = tmp_path / "evolved" / "attacker"
        evil_dir.mkdir(parents=True)
        (evil_dir / "evil.py").write_text("import os", encoding="utf-8")

        ok, issues = verify_manifest(
            manifest_path=tmp_path / "MANIFEST.sha256",
            public_key_b64=pub_b64,
            community_dir=tmp_path,
        )
        assert not ok
        assert any("Injected file" in i for i in issues)

    def test_forged_signature(self, tmp_path, keypair):
        """Signing with a DIFFERENT key should fail verification."""
        _, pub_b64 = keypair  # verify with this key
        other_priv, _ = _generate_test_keypair()  # sign with different key

        (tmp_path / "README.md").write_text("# Test", encoding="utf-8")
        manifest = generate_manifest(tmp_path)
        signed = sign_manifest(manifest, other_priv)  # wrong key!
        (tmp_path / "MANIFEST.sha256").write_text(signed, encoding="utf-8")

        ok, issues = verify_manifest(
            manifest_path=tmp_path / "MANIFEST.sha256",
            public_key_b64=pub_b64,
            community_dir=tmp_path,
        )
        assert not ok
        assert any("tampered" in i.lower() or "mismatch" in i.lower() for i in issues)

    def test_missing_manifest(self, tmp_path, keypair):
        _, pub_b64 = keypair
        ok, issues = verify_manifest(
            manifest_path=tmp_path / "MANIFEST.sha256",
            public_key_b64=pub_b64,
            community_dir=tmp_path,
        )
        assert not ok
        assert any("not found" in i for i in issues)

    def test_no_signature_line(self, tmp_path, keypair):
        """Manifest without signature line should fail."""
        _, pub_b64 = keypair
        (tmp_path / "MANIFEST.sha256").write_text(
            "abc123  README.md", encoding="utf-8"
        )
        ok, issues = verify_manifest(
            manifest_path=tmp_path / "MANIFEST.sha256",
            public_key_b64=pub_b64,
            community_dir=tmp_path,
        )
        assert not ok
        assert any("No signature" in i for i in issues)

    def test_deleted_file(self, tmp_path, keypair):
        """File in manifest but missing from disk should fail."""
        priv_pem, pub_b64 = keypair
        (tmp_path / "README.md").write_text("# Test", encoding="utf-8")
        (tmp_path / "extra.py").write_text("code", encoding="utf-8")

        manifest = generate_manifest(tmp_path)
        signed = sign_manifest(manifest, priv_pem)
        (tmp_path / "MANIFEST.sha256").write_text(signed, encoding="utf-8")

        # Delete
        (tmp_path / "extra.py").unlink()

        ok, issues = verify_manifest(
            manifest_path=tmp_path / "MANIFEST.sha256",
            public_key_b64=pub_b64,
            community_dir=tmp_path,
        )
        assert not ok
        assert any("Missing" in i for i in issues)

    def test_wrong_public_key_fails(self, tmp_path, keypair):
        """Verifying with wrong public key should fail."""
        priv_pem, _ = keypair
        _, other_pub_b64 = _generate_test_keypair()

        (tmp_path / "README.md").write_text("# Test", encoding="utf-8")
        manifest = generate_manifest(tmp_path)
        signed = sign_manifest(manifest, priv_pem)
        (tmp_path / "MANIFEST.sha256").write_text(signed, encoding="utf-8")

        ok, issues = verify_manifest(
            manifest_path=tmp_path / "MANIFEST.sha256",
            public_key_b64=other_pub_b64,
            community_dir=tmp_path,
        )
        assert not ok
        assert any("tampered" in i.lower() or "mismatch" in i.lower() for i in issues)


class TestVerifyCommunityIntegrity:
    def test_passes_on_valid(self, tmp_path, keypair):
        priv_pem, pub_b64 = keypair
        (tmp_path / "README.md").write_text("# OK", encoding="utf-8")
        manifest = generate_manifest(tmp_path)
        signed = sign_manifest(manifest, priv_pem)
        (tmp_path / "MANIFEST.sha256").write_text(signed, encoding="utf-8")

        # Patch the module-level key for this test
        import agos.evolution.manifest as m
        orig = m.VERIFICATION_PUBLIC_KEY_B64
        m.VERIFICATION_PUBLIC_KEY_B64 = pub_b64
        try:
            assert verify_community_integrity(community_dir=tmp_path)
        finally:
            m.VERIFICATION_PUBLIC_KEY_B64 = orig

    def test_fails_on_tampered(self, tmp_path, keypair):
        priv_pem, pub_b64 = keypair
        (tmp_path / "README.md").write_text("# OK", encoding="utf-8")
        manifest = generate_manifest(tmp_path)
        signed = sign_manifest(manifest, priv_pem)
        (tmp_path / "MANIFEST.sha256").write_text(signed, encoding="utf-8")
        (tmp_path / "README.md").write_text("# HACKED", encoding="utf-8")

        import agos.evolution.manifest as m
        orig = m.VERIFICATION_PUBLIC_KEY_B64
        m.VERIFICATION_PUBLIC_KEY_B64 = pub_b64
        try:
            assert not verify_community_integrity(community_dir=tmp_path)
        finally:
            m.VERIFICATION_PUBLIC_KEY_B64 = orig


class TestFileInManifest:
    def test_found(self, tmp_path):
        (tmp_path / "MANIFEST.sha256").write_text(
            "abc123  evolved/n1/tool.py\n# SIGNATURE: dGVzdA==",
            encoding="utf-8",
        )
        assert file_in_manifest("evolved/n1/tool.py", manifest_path=tmp_path / "MANIFEST.sha256")

    def test_not_found(self, tmp_path):
        (tmp_path / "MANIFEST.sha256").write_text(
            "abc123  evolved/n1/tool.py\n# SIGNATURE: dGVzdA==",
            encoding="utf-8",
        )
        assert not file_in_manifest("evolved/attacker/evil.py", manifest_path=tmp_path / "MANIFEST.sha256")

    def test_no_manifest(self, tmp_path):
        assert not file_in_manifest("anything.py", manifest_path=tmp_path / "nope")


class TestAsymmetricSecurity:
    """Tests proving the asymmetric security model works."""

    def test_attacker_cannot_sign_with_public_key(self, keypair):
        """The public key in source code cannot be used to sign."""
        _, pub_b64 = keypair
        # An attacker has the public key (from source) but not the private key.
        # They cannot call sign_manifest because it requires a PEM private key.
        with pytest.raises((ValueError, Exception)):
            sign_manifest("evil manifest", pub_b64)  # pub key is not a PEM private key

    def test_different_keys_cannot_forge(self, keypair):
        """Even with a valid Ed25519 key, a different key can't forge."""
        _, pub_b64 = keypair
        attacker_priv, _ = _generate_test_keypair()

        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "README.md").write_text("legit content", encoding="utf-8")
            manifest = generate_manifest(td)
            # Attacker signs with their own key
            signed = sign_manifest(manifest, attacker_priv)
            (td / "MANIFEST.sha256").write_text(signed, encoding="utf-8")

            # Verification with the real public key fails
            ok, issues = verify_manifest(
                manifest_path=td / "MANIFEST.sha256",
                public_key_b64=pub_b64,
                community_dir=td,
            )
            assert not ok
