"""TokenPak messages for AutoGen."""

from typing import Any, Optional, Dict


class TokenPakMessage:
    """Wraps TokenPak data as AutoGen message."""
    
    def __init__(
        self,
        pack: Optional[Dict[str, Any]] = None,
        content: Optional[str] = None,
    ):
        self.pack = pack
        self.content = content or ""
    
    def to_string(self) -> str:
        """Convert to string representation."""
        if self.pack:
            return f"[TokenPak: {len(str(self.pack))} bytes]"
        return self.content
    
    def __str__(self) -> str:
        return self.to_string()
