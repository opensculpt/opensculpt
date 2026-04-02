"""Community manifest — sign and verify community contributions.

After a PR is merged to opensculpt/opensculpt, a GitHub Action generates
MANIFEST.sha256 listing every file in community/ with its SHA256 hash,
then signs it with Ed25519. Clients verify using the embedded public key.

Security model (asymmetric — C1 fix):
  - Private key: stored ONLY in GitHub repo secret (COMMUNITY_SIGNING_KEY)
  - Public key: embedded in this source file
  - An attacker who reads the source gets the public key, which can only
    VERIFY signatures, not CREATE them. Forging a manifest requires the
    private key, which never leaves GitHub Actions.

This prevents:
  - Local file injection (attacker drops .py into community/evolved/)
  - Forked repo poisoning (attacker changes files but can't sign)
  - MITM on git clone (signature won't match tampered content)
"""

from __future__ import annotations

import base64
import hashlib
import logging
from pathlib import Path

_logger = logging.getLogger(__name__)

MANIFEST_PATH = Path("community/MANIFEST.sha256")
COMMUNITY_DIR = Path("community")

# Ed25519 public key (raw 32 bytes, base64-encoded).
# The corresponding private key is in GitHub secret COMMUNITY_SIGNING_KEY.
# This key can VERIFY signatures but CANNOT create them.
VERIFICATION_PUBLIC_KEY_B64 = "OYgO+0ONjV/DH2Jot4ZfvabySlgv/8gsC+w5bFkAC2w="


def hash_file(path: Path) -> str:
    """SHA256 hex digest of a file's content."""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def generate_manifest(community_dir: Path | None = None) -> str:
    """Generate a manifest listing SHA256 hashes of all community files.

    Format matches `sha256sum` output:
        <hash>  <relative_path>

    Excludes MANIFEST.sha256 itself and .gitkeep files.
    """
    root = community_dir or COMMUNITY_DIR
    lines = []

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name == "MANIFEST.sha256":
            continue
        if path.name == ".gitkeep":
            continue

        rel = path.relative_to(root)
        h = hash_file(path)
        lines.append(f"{h}  {rel.as_posix()}")

    return "\n".join(lines)


def sign_manifest(manifest_text: str, private_key_pem: str | None = None) -> str:
    """Sign a manifest with Ed25519. Returns manifest + base64 signature line.

    Args:
        manifest_text: The manifest body to sign.
        private_key_pem: PEM-encoded Ed25519 private key. In production this
            comes from the COMMUNITY_SIGNING_KEY GitHub secret.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    if private_key_pem is None:
        raise ValueError("Private key required for signing (set COMMUNITY_SIGNING_KEY)")

    key = load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Key must be Ed25519")

    sig_bytes = key.sign(manifest_text.encode("utf-8"))
    sig_b64 = base64.b64encode(sig_bytes).decode("ascii")
    return f"{manifest_text}\n# SIGNATURE: {sig_b64}"


def _parse_manifest(manifest_text: str) -> tuple[str, str, dict[str, str]]:
    """Parse a signed manifest into (body, signature_b64, file_hashes).

    Returns:
        body: the manifest text without the signature line
        signature: base64-encoded Ed25519 signature
        file_hashes: dict of relative_path -> sha256_hex
    """
    lines = manifest_text.strip().split("\n")
    signature = ""
    body_lines = []
    file_hashes: dict[str, str] = {}

    for line in lines:
        if line.startswith("# SIGNATURE: "):
            signature = line[len("# SIGNATURE: "):].strip()
        else:
            body_lines.append(line)
            parts = line.split("  ", 1)
            if len(parts) == 2:
                file_hashes[parts[1].strip()] = parts[0].strip()

    body = "\n".join(body_lines)
    return body, signature, file_hashes


def verify_manifest(
    manifest_path: Path | None = None,
    public_key_b64: str | None = None,
    community_dir: Path | None = None,
) -> tuple[bool, list[str]]:
    """Verify a signed manifest against disk.

    Checks:
    1. Ed25519 signature is valid (requires the matching private key to forge)
    2. Every file in manifest exists on disk with matching hash
    3. No extra files on disk that aren't in manifest (injection detection)

    Returns (ok, issues).
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature

    mpath = manifest_path or MANIFEST_PATH
    cdir = community_dir or COMMUNITY_DIR
    pub_b64 = public_key_b64 or VERIFICATION_PUBLIC_KEY_B64
    issues: list[str] = []

    if not mpath.exists():
        return False, ["MANIFEST.sha256 not found"]

    try:
        content = mpath.read_text(encoding="utf-8")
    except Exception as e:
        return False, [f"Cannot read manifest: {e}"]

    body, signature, file_hashes = _parse_manifest(content)

    if not signature:
        issues.append("No signature found in manifest")
        return False, issues

    # 1. Verify Ed25519 signature
    try:
        pub_bytes = base64.b64decode(pub_b64)
        public_key = Ed25519PublicKey.from_public_bytes(pub_bytes)
        sig_bytes = base64.b64decode(signature)
        public_key.verify(sig_bytes, body.encode("utf-8"))
    except InvalidSignature:
        issues.append("Signature mismatch — manifest has been tampered with")
        return False, issues
    except Exception as e:
        issues.append(f"Signature verification error: {e}")
        return False, issues

    # 2. Verify each file hash
    for rel_path, expected_hash in file_hashes.items():
        full_path = cdir / rel_path
        if not full_path.exists():
            issues.append(f"Missing: {rel_path}")
            continue
        actual_hash = hash_file(full_path)
        if actual_hash != expected_hash:
            issues.append(f"Hash mismatch: {rel_path}")

    # 3. Detect injected files (on disk but not in manifest)
    for path in sorted(cdir.rglob("*")):
        if not path.is_file():
            continue
        if path.name in ("MANIFEST.sha256", ".gitkeep"):
            continue
        rel = path.relative_to(cdir).as_posix()
        if rel not in file_hashes:
            issues.append(f"Injected file (not in manifest): {rel}")

    ok = len(issues) == 0
    if not ok:
        _logger.warning("Manifest verification failed: %s", issues)
    return ok, issues


def verify_community_integrity(
    community_dir: Path | None = None,
) -> bool:
    """High-level check: is the community/ directory trustworthy?

    Returns True if manifest exists and passes all checks.
    """
    cdir = community_dir or COMMUNITY_DIR
    mpath = (community_dir / "MANIFEST.sha256") if community_dir else MANIFEST_PATH

    ok, issues = verify_manifest(
        manifest_path=mpath,
        community_dir=cdir,
    )
    if not ok:
        for issue in issues:
            _logger.error("Community integrity: %s", issue)
    return ok


def file_in_manifest(rel_path: str, manifest_path: Path | None = None) -> bool:
    """Check if a specific file is listed in the manifest."""
    mpath = manifest_path or MANIFEST_PATH
    if not mpath.exists():
        return False
    try:
        content = mpath.read_text(encoding="utf-8")
        _, _, file_hashes = _parse_manifest(content)
        return rel_path in file_hashes
    except Exception:
        return False
