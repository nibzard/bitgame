"""Manifest parsing and validation (SPEC.md section 7).

The canonical serialization — sorted keys, no whitespace — is what gets
hashed and signed. Ed25519 verification is a host duty; this module checks
structure, budget maxima, and the closed permission set.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional, Set

from meshkit.budgets import Budget
from meshkit.events import canonical_json

PERMISSIONS: Set[str] = {
    "mesh.broadcast",
    "mesh.direct",
    "state.persist",
    "board.post",
    "value.cashu.receive",
    "value.cashu.send",
}

REQUIRED_FIELDS = (
    "manifestVersion",
    "appId",
    "version",
    "name",
    "entry",
    "moduleHash",
    "signer",
    "budget",
    "permissions",
)


@dataclass
class Manifest:
    raw: dict
    budget: Budget

    @property
    def app_id(self) -> str:
        return self.raw["appId"]

    @property
    def permissions(self) -> Set[str]:
        return set(self.raw["permissions"])

    def canonical_bytes(self) -> bytes:
        """Canonical form of the manifest without the signature field."""
        payload = {k: v for k, v in self.raw.items() if k != "signature"}
        return canonical_json(payload)

    def content_hash(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_bytes()).hexdigest()

    def module_bytes_hash(self, module: bytes) -> str:
        return "sha256:" + hashlib.sha256(module).hexdigest()


def validate_manifest(data: dict) -> Optional[str]:
    """Return None when valid, otherwise a human-readable reason."""
    if not isinstance(data, dict):
        return "manifest must be a JSON object"
    for name in REQUIRED_FIELDS:
        if name not in data:
            return f"missing field: {name}"
    if data.get("manifestVersion") != 1:
        return "unsupported manifestVersion"
    app_id = data.get("appId", "")
    if not isinstance(app_id, str) or not app_id or len(app_id.encode()) > 64:
        return "appId must be 1..64 bytes"
    if "." not in app_id:
        return "appId must be reverse-DNS"
    if not isinstance(data.get("permissions"), list):
        return "permissions must be a list"
    for permission in data["permissions"]:
        if permission not in PERMISSIONS:
            return f"unknown permission: {permission}"
    budget = Budget.from_manifest(data["budget"])
    problem = budget.validate()
    if problem:
        return problem
    module_hash = data.get("moduleHash", "")
    if not isinstance(module_hash, str) or not module_hash.startswith("sha256:"):
        return "moduleHash must be sha256:<hex>"
    signer = data.get("signer", "")
    if not isinstance(signer, str) or not signer.startswith("ed25519:"):
        return "signer must be ed25519:<hex>"
    return None


def parse_manifest(data: dict) -> Manifest:
    problem = validate_manifest(data)
    if problem:
        raise ValueError(problem)
    return Manifest(raw=data, budget=Budget.from_manifest(data["budget"]))
