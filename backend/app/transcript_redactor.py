"""Regex-based PII redactor for transcript turns.

Applied to user/agent text in :func:`TranscriptSink.flush` right
before the Cosmos upsert, so sensitive content never lands in
storage. Counts of redactions are persisted on the document and
emitted to the audit log so operators can watch hit rates without
needing access to the redacted text itself.

Toggle
------
``TRANSCRIPT_PII_REDACTION`` env var. Default: ``true``. Set to
``false`` to store transcript text verbatim (e.g. for an agent
where PII will never appear and the analytics value of raw text
outweighs the privacy cost). Read at call time so tests / runtime
overrides take effect without reimporting the module.

Patterns
--------
The default rule set is intentionally narrow -- high-precision
patterns where the false-positive cost is acceptable:

    email        RFC-ish, common shape
    phone_us     10-digit North American format with separators
    ssn          US SSN ``xxx-xx-xxxx`` with format validator
    credit_card  13-19 digit Luhn-valid number
    ipv4         dotted quad with strict 0-255 octets

Each match is replaced with a literal placeholder like
``[EMAIL_REDACTED]`` so a reviewer can see *that* a field was
present without seeing its value.

Extending
---------
Append a tuple to :data:`PATTERNS`. The pattern's ``name`` shows
up verbatim in the per-flush counts dict and as
``[NAME_REDACTED]`` in the output text.

Limitations
-----------
Names, addresses, free-form identifiers, and LLM-inferred PII are
out of scope. Wire Presidio or a similar NER pipeline if you need
those.
"""

from __future__ import annotations

import os
import re
from typing import Callable, Pattern

_ENV_VAR = "TRANSCRIPT_PII_REDACTION"


def is_enabled() -> bool:
    """Read the toggle at call time. Default: enabled."""
    return os.getenv(_ENV_VAR, "true").lower() == "true"


# Validators reject false positives that the regex alone can't filter.
def _ssn_valid(m: str) -> bool:
    """Format-level SSA exclusions (https://www.ssa.gov/employer/randomization.html)."""
    digits = m.replace("-", "")
    if len(digits) != 9:
        return False
    area, group, serial = digits[:3], digits[3:5], digits[5:]
    if area in ("000", "666") or area.startswith("9"):
        return False
    if group == "00":
        return False
    if serial == "0000":
        return False
    return True


def _luhn_valid(m: str) -> bool:
    digits = [int(c) for c in m if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


# (name, compiled_pattern, optional_validator)
PATTERNS: list[tuple[str, Pattern[str], Callable[[str], bool] | None]] = [
    (
        "email",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        None,
    ),
    # US-style phone: (xxx) xxx-xxxx, xxx-xxx-xxxx, xxx.xxx.xxxx, 1-xxx-xxx-xxxx.
    # `(?<!\d)` / `(?!\d)` keep us from biting into longer numeric runs.
    (
        "phone_us",
        re.compile(r"(?<!\d)(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}(?!\d)"),
        None,
    ),
    (
        "ssn",
        re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"),
        _ssn_valid,
    ),
    # Credit card: 13-19 digits with optional space/dash separators between
    # groups. Luhn-validated to keep FPs to a minimum.
    (
        "credit_card",
        re.compile(r"(?<!\d)(?:\d[\s-]?){12,18}\d(?!\d)"),
        _luhn_valid,
    ),
    # IPv4 — strict 0-255 octets, word-boundaried.
    (
        "ipv4",
        re.compile(
            r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
            r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b"
        ),
        None,
    ),
]


def redact(text: str) -> tuple[str, dict[str, int]]:
    """Return ``(redacted_text, counts_by_pattern)``.

    Returns the input unchanged when redaction is disabled or
    ``text`` is empty. The counts dict only carries keys for
    patterns that actually matched at least once.

    Patterns are applied in :data:`PATTERNS` order, and each
    replacement substitutes ``[NAME_REDACTED]`` so subsequent
    patterns won't accidentally re-match the placeholder.
    """
    if not is_enabled() or not text:
        return text, {}

    counts: dict[str, int] = {}
    redacted = text
    for name, regex, validator in PATTERNS:
        # Closure captures `name` and `validator` from the current
        # loop iteration. `regex.sub` is synchronous, so all `_repl`
        # calls finish before the next iteration rebinds them.
        def _repl(match: re.Match[str], _name: str = name,
                  _validator: Callable[[str], bool] | None = validator) -> str:
            value = match.group(0)
            if _validator is not None and not _validator(value):
                return value
            counts[_name] = counts.get(_name, 0) + 1
            return f"[{_name.upper()}_REDACTED]"
        redacted = regex.sub(_repl, redacted)
    return redacted, counts
