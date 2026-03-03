"""
TokenPak Credential Passthrough

CRITICAL SECURITY MODULE: Handles credential forwarding with ZERO storage.

Rules:
- Headers forwarded untouched to providers
- ZERO credential storage (not in memory longer than request lifecycle)
- ZERO credential logging (not even at debug level)
- No API calls made using user credentials by this module
"""

from dataclasses import dataclass
from typing import Dict, Set, Optional
import os


@dataclass
class PassthroughConfig:
    """Configuration for credential passthrough behavior."""
    
    # Headers to strip (never forward to backend)
    strip_headers: Set[str] = None
    
    # Headers to log (for debugging - NEVER include auth headers)
    safe_to_log: Set[str] = None
    
    def __post_init__(self):
        if self.strip_headers is None:
            self.strip_headers = {
                "host",
                "proxy-connection", 
                "proxy-authorization",
                "connection",
                "keep-alive",
                "transfer-encoding",
                "te",
                "trailer",
                "upgrade",
                "content-length",
                "accept-encoding",
            }
        if self.safe_to_log is None:
            self.safe_to_log = {
                "content-type",
                "anthropic-version",
                "user-agent",
            }


# Default config instance
_DEFAULT_CONFIG = PassthroughConfig()


def forward_headers(
    incoming_headers: Dict[str, str],
    target_host: str,
    body_length: Optional[int] = None,
    config: Optional[PassthroughConfig] = None,
) -> Dict[str, str]:
    """
    Forward headers to target host, stripping proxy-specific headers.
    
    SECURITY: This function handles credentials. It:
    - Forwards auth headers (x-api-key, Authorization) UNTOUCHED
    - Does NOT log, store, or inspect credential values
    - Does NOT hold references beyond the return value
    
    Args:
        incoming_headers: Headers from the incoming request
        target_host: Host to forward to (for Host header)
        body_length: Optional body length for Content-Length header
        config: Optional PassthroughConfig (uses defaults if None)
    
    Returns:
        Dict of headers to forward to backend
    """
    if config is None:
        config = _DEFAULT_CONFIG
    
    forwarded = {}
    
    for key, value in incoming_headers.items():
        # Skip headers we should strip
        if key.lower() in config.strip_headers:
            continue
        
        # Forward everything else UNTOUCHED
        # This includes x-api-key, Authorization, etc.
        forwarded[key] = value
    
    # Set the correct Host header for the target
    forwarded["Host"] = target_host
    
    # Set Content-Length if body is present
    if body_length is not None:
        forwarded["Content-Length"] = str(body_length)
    
    return forwarded


def mask_for_logging(headers: Dict[str, str], config: Optional[PassthroughConfig] = None) -> Dict[str, str]:
    """
    Create a safe-to-log version of headers with credentials masked.
    
    Use this ONLY when you need to log headers for debugging.
    Never log the raw headers dict when it may contain credentials.
    
    Args:
        headers: Headers dict (may contain sensitive values)
        config: Optional PassthroughConfig
    
    Returns:
        Dict with sensitive values replaced by "[REDACTED]"
    """
    if config is None:
        config = _DEFAULT_CONFIG
    
    masked = {}
    for key, value in headers.items():
        key_lower = key.lower()
        
        # Only include safe-to-log headers with their values
        if key_lower in config.safe_to_log:
            masked[key] = value
        # For all others, redact the value
        elif "key" in key_lower or "auth" in key_lower or "token" in key_lower:
            masked[key] = "[REDACTED]"
        else:
            # Include the header name but mask potentially sensitive values
            # This helps with debugging without leaking secrets
            if len(value) > 20:
                masked[key] = f"{value[:4]}...[{len(value)} chars]"
            else:
                masked[key] = value
    
    return masked


# Ensure no credential-related environment variables are accidentally exposed
def get_safe_env_for_logging() -> Dict[str, str]:
    """
    Get environment variables that are safe to log.
    Excludes anything that looks like a credential.
    """
    unsafe_patterns = {"key", "secret", "token", "password", "auth", "credential"}
    
    safe_env = {}
    for key, value in os.environ.items():
        key_lower = key.lower()
        if any(pattern in key_lower for pattern in unsafe_patterns):
            continue
        safe_env[key] = value
    
    return safe_env
