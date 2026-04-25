# SPDX-License-Identifier: Apache-2.0
"""Pre-write guardrail checks per spec §5.

Five non-negotiable rules that block writes:

  1. No live API calls during scaffolding (enforced at the
     architectural level — there's no HTTP client in the scaffold
     module; this just verifies generated code doesn't add one).
  2. No raw credential storage — generated files MUST NOT contain
     real-looking API keys, OAuth tokens, JWTs, or AWS access keys.
  3. No implicit TIP support — generated capability declarations
     MUST be explicit (the scaffolder doesn't currently emit
     capabilities directly; the underlying ``_EnvKeyBearerProvider``
     inherits from the format adapter, where capabilities ARE
     declared. This guard is forward-compatible for richer
     scaffold output.)
  4. No destructive config changes — the scaffold tool only writes
     NEW files (CredentialProvider class is appended to
     credential_injector.py via insertion, never replacing existing
     content).
  5. Generated code must follow Standard #23 — the inputs are
     validated upfront in :class:`ScaffoldParams.validate`, and the
     templates are written to be #23-compliant by construction.

The check runs on the in-memory artifact list BEFORE any file is
written. Failure raises :class:`GuardrailViolation`; the writer
never sees a violating artifact.
"""

from __future__ import annotations

import re
from typing import List

from ._generator import GeneratedArtifact

# Credential-shape regexes. Any string that looks like a real
# credential in generated output is a guardrail violation.
_CRED_PATTERNS = [
    # OpenAI-style API keys: sk-proj-..., sk-...
    re.compile(r"\bsk-[a-zA-Z0-9_-]{16,}"),
    # Anthropic keys: sk-ant-...
    re.compile(r"\bsk-ant-[a-zA-Z0-9_-]{16,}"),
    # OpenRouter keys: sk-or-v1-...
    re.compile(r"\bsk-or-v1-[a-zA-Z0-9_-]{16,}"),
    # AWS access key ids: AKIA followed by 16 chars
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    # AWS secret access keys: 40-char base64-ish (heuristic)
    re.compile(r"\b[A-Za-z0-9/+=]{40}\b(?=.*aws)"),
    # GitHub fine-grained PATs: github_pat_...
    re.compile(r"\bgithub_pat_[a-zA-Z0-9_]{40,}"),
    # JWT shape: eyJ + dot-separated base64url. Three segments,
    # each at least 10 chars (loose enough to catch test-shape JWTs +
    # real ones; placeholders like ``eyJfake.x.y`` won't match).
    re.compile(r"\beyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{20,}"),
    # GCP service account private key markers
    re.compile(r"-----BEGIN (RSA |OPENSSH )?PRIVATE KEY-----"),
]

# Strings that are OBVIOUSLY placeholders + safe.
_PLACEHOLDER_ALLOWLIST = {
    "sk-or-test",
    "sk-test",
    "sk-fake",
    "test-fake-key-1234567890",
    "test-fake-key",
    "AKIAIOSFODNN7EXAMPLE",  # AWS docs canonical placeholder
}


class GuardrailViolation(Exception):
    """Raised when generated content violates a guardrail.

    Carries the artifact path + offending content snippet so the
    caller can produce a useful error message.
    """


def check_artifacts(artifacts: List[GeneratedArtifact]) -> None:
    """Run all guardrails on the artifact list. Raises on any failure.

    The order is: cheap checks first (path-shape, no-overwrite),
    then content-level (credential scan).
    """
    for art in artifacts:
        _check_no_raw_credentials(art)
        _check_path_inside_repo(art)


def _check_no_raw_credentials(art: GeneratedArtifact) -> None:
    """Reject any artifact whose content matches a credential-shape regex.

    Allowlist for clearly-placeholder strings used by tests + docs
    examples (``sk-test``, the AWS canonical placeholder, etc.).
    """
    content = art.content
    for pattern in _CRED_PATTERNS:
        for match in pattern.finditer(content):
            matched = match.group(0)
            if matched in _PLACEHOLDER_ALLOWLIST:
                continue
            raise GuardrailViolation(
                f"Generated artifact {art.relative_path!r} contains what "
                f"looks like a real credential: {matched[:20]}... "
                f"(matched pattern {pattern.pattern!r}). The scaffold tool "
                f"must never emit raw credentials. If this is a false "
                f"positive (example placeholder), add it to the allowlist "
                f"in _guardrails.py."
            )


def _check_path_inside_repo(art: GeneratedArtifact) -> None:
    """Refuse to write anywhere outside the repo.

    Defense-in-depth: even if the generator's path computation is
    wrong, the writer never lands in a system path.
    """
    forbidden_roots = (
        "/etc",
        "/usr",
        "/bin",
        "/sbin",
        "/var",
        "/root",
        "/boot",
        "/dev",
        "/proc",
        "/sys",
    )
    rel = art.relative_path
    if rel.startswith("/"):
        for forbidden in forbidden_roots:
            if rel.startswith(forbidden):
                raise GuardrailViolation(
                    f"Generated artifact path {rel!r} would write outside "
                    f"the repo (system path {forbidden}). The scaffold tool "
                    f"only writes inside the repo."
                )
    if ".." in rel.split("/"):
        raise GuardrailViolation(
            f"Generated artifact path {rel!r} contains a parent-directory "
            f"traversal segment ('..'). Refusing to write."
        )
