# SPDX-License-Identifier: Apache-2.0
"""
tokenpak.security
=================

Security subpackage for TokenPak.
Exports the DLP scanner and supporting types.

Free-tier subset of the I4 Security/PII/DLP architecture component:
gitleaks-pattern secret scanner (warn/redact/block modes).
Full PII/DLP remains Enterprise (I4).
"""
from tokenpak.security.dlp import DLPScanner, DLPMatch, DLPBlockError

__all__ = ["DLPScanner", "DLPMatch", "DLPBlockError"]
