"""The run's seal (sealed trial): an Ed25519 signature over everything that must not change
once the world is launched.

On day 0, `sovereign-world seal` signs a document holding the manifest hash, the code's two
hashes, the versions, the prompt version, the reply schema's hash, each civilization's charter
hash, where each model is reached (provider pins), the budgets, the spending cap, the planned
days and the start rotation, with the public key. It is saved in SQLite and as the journal's
record right after the header. `run` then refuses (`SealRefused`) a run whose manifest, seal,
code or providers no longer match it, or that has reached its planned days; `verify --signer`
checks who sealed it.

The private key lives only in the environment variable `SOVEREIGN_WORLD_SEAL_KEY`, which only
`seal` reads; it is never written, printed or passed to a child process. Someone holding the
key could seal a changed run anew: the fingerprint the operator checks with `verify --signer`
is what ties a run to its key.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict, Field

from sovereign_world.config import BudgetConfig, RunManifest, SpendConfig
from sovereign_world.persistence import HEADER, SEAL, WorldStore
from sovereign_world.state import WorldState

SEAL_KEY_ENV = "SOVEREIGN_WORLD_SEAL_KEY"
KEY_PREFIX = "swseal1:"
SEAL_VERSION = 1
HOSTED_BASE_URLS = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com/v1",
}
"""Where the hosted providers' clients are pointed in a sealed run, whatever the environment
says (``ANTHROPIC_BASE_URL`` and ``OPENAI_BASE_URL`` then have no effect)."""
HOSTED_TOKEN_ENVS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
CLI_PROGRAMS = {"claude-code": "claude", "codex": "codex"}
"""Kinds played by a vendor's own program on this computer, signed in with the user's plan: the
pin names the program (``cli://claude``), not where it is installed."""


class SealKeyMissing(RuntimeError):
    """The seal key is not in the environment."""


class SealKeyInvalid(RuntimeError):
    """The environment holds something that is not a seal key (its value is never shown)."""


class SealRefused(RuntimeError):
    """The run no longer matches its seal, or has reached its planned days."""


def _canonical(document: Mapping[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fingerprint_of(public_key_hex: str) -> str:
    """The sha256 of the raw 32-byte public key, in hex."""
    return hashlib.sha256(bytes.fromhex(public_key_hex)).hexdigest()


def grouped(fingerprint: str, groups: int | None = None) -> str:
    """A fingerprint in groups of four hex digits (the first `groups` of them, if given)."""
    parts = [fingerprint[index : index + 4] for index in range(0, len(fingerprint), 4)]
    return " ".join(parts[:groups] if groups is not None else parts)


def normalise_fingerprint(text: str) -> str:
    """A fingerprint as typed (spaces, colons, any case) in its plain form; the whole of it."""
    plain = "".join(char for char in text.lower() if char not in " :")
    if len(plain) != 64 or any(char not in "0123456789abcdef" for char in plain):
        raise ValueError("give the whole fingerprint: 64 hex digits, spaces and colons allowed")
    return plain


def _public_hex(key: Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes_raw().hex()


def generate_key() -> tuple[str, str]:
    """A new seal key, as the environment variable's value, and its fingerprint."""
    key = Ed25519PrivateKey.generate()
    seed = base64.urlsafe_b64encode(key.private_bytes_raw()).decode("ascii").rstrip("=")
    return KEY_PREFIX + seed, fingerprint_of(_public_hex(key))


def load_key(environ: Mapping[str, str]) -> Ed25519PrivateKey:
    """The seal key from `SOVEREIGN_WORLD_SEAL_KEY`; errors never show the value."""
    value = environ.get(SEAL_KEY_ENV)
    if not value:
        raise SealKeyMissing(
            f"{SEAL_KEY_ENV} is not set; put the key `sovereign-world keygen` printed in it"
        )
    if not value.startswith(KEY_PREFIX):
        raise SealKeyInvalid(f"{SEAL_KEY_ENV} does not hold a seal key (one starts {KEY_PREFIX})")
    encoded = value[len(KEY_PREFIX) :]
    try:
        seed = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except (binascii.Error, ValueError):
        raise SealKeyInvalid(f"{SEAL_KEY_ENV} does not hold a readable seal key") from None
    if len(seed) != 32:
        raise SealKeyInvalid(f"{SEAL_KEY_ENV} does not hold a seal key of the right length")
    return Ed25519PrivateKey.from_private_bytes(seed)


class ProviderPin(BaseModel):
    """Where a model-played civilization's model is reached, as sealed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    model: str
    scheme: str
    host: str
    """The host name, with its port when one is given."""
    path: str
    token_env: str | None = None
    """The name of the variable holding the token (never the token)."""

    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}{self.path}"


def _pin(kind: str, model: str, base_url: str, token_env: str | None) -> ProviderPin:
    parts = urlsplit(base_url)
    if not parts.scheme or not parts.hostname:
        raise ValueError(f"{base_url!r} is not an address a model can be reached at")
    host = parts.hostname.lower() + (f":{parts.port}" if parts.port is not None else "")
    return ProviderPin(
        kind=kind,
        model=model,
        scheme=parts.scheme.lower(),
        host=host,
        path=parts.path.rstrip("/"),
        token_env=token_env,
    )


def pins_of(manifest: RunManifest) -> dict[str, ProviderPin]:
    """Each model-played civilization's provider pin."""
    pins: dict[str, ProviderPin] = {}
    for civilization_id, config in sorted(manifest.sovereigns.items()):
        if config.provider == "baseline":
            continue
        if config.provider in CLI_PROGRAMS:
            pins[civilization_id] = ProviderPin(
                kind=config.provider,
                model=config.model,
                scheme="cli",
                host=CLI_PROGRAMS[config.provider],
                path="",
            )
        elif config.provider in HOSTED_BASE_URLS:
            pins[civilization_id] = _pin(
                config.provider,
                config.model,
                HOSTED_BASE_URLS[config.provider],
                HOSTED_TOKEN_ENVS[config.provider],
            )
        else:
            pins[civilization_id] = _pin(
                config.provider, config.model, config.base_url, config.token_env
            )
    return pins


class Seal(BaseModel):
    """The signed seal document."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    seal_version: int = SEAL_VERSION
    run_id: str
    manifest_hash: str
    code_hash: str
    """``rulehash.rule_hash``: everything that runs the world."""
    engine_hash: str
    """``rulehash.engine_hash``: what decides it (what the balance calibration measured)."""
    versions: dict[str, str | int]
    prompt_version: str
    reply_schema_hash: str
    charters: dict[str, str]
    """Each civilization's charter (system prompt) hash; constant for the run."""
    providers: dict[str, ProviderPin]
    budgets: BudgetConfig
    spend: SpendConfig | None = None
    planned_days: int = Field(ge=1)
    start_rotation: int = 0
    public_key: str
    signature: str = ""

    def canonical_bytes(self) -> bytes:
        """What is signed: the document without its signature, as canonical JSON."""
        return _canonical(self.model_dump(mode="json", exclude={"signature"}))

    @property
    def fingerprint(self) -> str:
        return fingerprint_of(self.public_key)

    def verify_signature(self) -> None:
        try:
            public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(self.public_key))
            public.verify(bytes.fromhex(self.signature), self.canonical_bytes())
        except (InvalidSignature, ValueError):
            raise SealRefused("the seal's signature does not verify") from None


def _versions(manifest: RunManifest) -> dict[str, str | int]:
    return {
        "engine": manifest.engine_version,
        "generator": manifest.generator_version,
        "rules": manifest.rules_version,
        "journal_format": manifest.journal_format,
    }


def charter_hashes(state: WorldState) -> dict[str, str]:
    """Each civilization's charter hash on `state` (the charter depends only on who it is and
    the rules version, so it holds for the whole run)."""
    from sovereign_world.commands import build_council_report
    from sovereign_world.gateway.prompt import charter

    return {
        str(civilization_id): _sha256(charter(build_council_report(state, civilization_id)))
        for civilization_id in sorted(state.civilizations)
    }


def reply_schema_hash() -> str:
    from sovereign_world.gateway.envelope import reply_schema

    return hashlib.sha256(_canonical(reply_schema())).hexdigest()


def make_seal(
    manifest: RunManifest,
    state: WorldState,
    *,
    planned_days: int,
    key: Ed25519PrivateKey,
    code_hash: str,
    engine_hash: str,
) -> Seal:
    """The signed seal of a run on day 0."""
    from sovereign_world.gateway.prompt import PROMPT_VERSION

    unsigned = Seal(
        run_id=str(manifest.run_id),
        manifest_hash=manifest.content_hash(),
        code_hash=code_hash,
        engine_hash=engine_hash,
        versions=_versions(manifest),
        prompt_version=PROMPT_VERSION,
        reply_schema_hash=reply_schema_hash(),
        charters=charter_hashes(state),
        providers=pins_of(manifest),
        budgets=manifest.budgets,
        spend=manifest.spend,
        planned_days=planned_days,
        start_rotation=manifest.start_rotation,
        public_key=_public_hex(key),
    )
    signature = key.sign(unsigned.canonical_bytes()).hex()
    return unsigned.model_copy(update={"signature": signature})


def stored_seal(store: WorldStore) -> Seal | None:
    """The run's seal, or None if it is not sealed."""
    document = store.seal_document()
    return None if document is None else Seal.model_validate(document)


def check_seal(
    store: WorldStore,
    manifest: RunManifest,
    seal: Seal,
    *,
    code_hash: str,
    signer: str | None = None,
    strict_code: bool = True,
) -> list[str]:
    """Refuse (`SealRefused`) a run that no longer matches its seal; return notes, such as code
    that differs when `strict_code` is off (an archived run still verifies on later code)."""
    seal.verify_signature()
    if signer is not None and seal.fingerprint != normalise_fingerprint(signer):
        raise SealRefused(
            f"the run was sealed by {grouped(seal.fingerprint)}, not by {grouped(signer)}"
        )
    expected: list[tuple[str, object, object]] = [
        ("run id", seal.run_id, str(manifest.run_id)),
        ("manifest hash", seal.manifest_hash, manifest.content_hash()),
        ("versions", seal.versions, _versions(manifest)),
        ("budgets", seal.budgets, manifest.budgets),
        ("spending cap", seal.spend, manifest.spend),
        ("start rotation", seal.start_rotation, manifest.start_rotation),
        ("providers", seal.providers, pins_of(manifest)),
    ]
    for name, sealed, actual in expected:
        if sealed != actual:
            raise SealRefused(f"the run's {name} no longer matches its seal")
    records = store.read_records()
    seal_records = [record for record in records if record.type == SEAL]
    if (
        len(records) < 2
        or records[0].type != HEADER
        or records[1].type != SEAL
        or len(seal_records) != 1
    ):
        raise SealRefused("the journal does not hold the seal right after its header")
    if Seal.model_validate(records[1].payload) != seal:
        raise SealRefused("the seal in the journal differs from the one saved with the run")
    if records[0].payload.get("manifest_hash") != manifest.content_hash():
        raise SealRefused("the journal's header names another manifest")
    notes: list[str] = []
    if seal.code_hash != code_hash:
        if strict_code:
            raise SealRefused(
                "this code differs from the code the run was sealed with"
                f" (code hash {code_hash[:12]}…, sealed {seal.code_hash[:12]}…)"
            )
        notes.append("the code differs from the code the run was sealed with")
    return notes
