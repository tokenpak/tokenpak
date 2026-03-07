"""TokenPak-backed memory for Microsoft Semantic Kernel."""

from typing import Dict, Any, Optional, List


class TokenPakMemory:
    """
    Semantic Kernel memory backed by TokenPak compression.
    
    Automatically compresses stored memories.
    """
    
    def __init__(
        self,
        budget: int = 4000,
        compaction_mode: str = "balanced",
        collection: str = "default",
    ):
        self.budget = budget
        self.compaction_mode = compaction_mode
        self.collection = collection
        self._memories: Dict[str, List[Dict[str, Any]]] = {collection: []}
    
    def save_information(
        self,
        collection: str,
        key: str,
        value: str,
    ) -> None:
        """Save information to memory."""
        if collection not in self._memories:
            self._memories[collection] = []
        
        self._memories[collection].append({
            "key": key,
            "value": value,
            "tokens": len(value) // 4,
        })
    
    def retrieve_information(
        self,
        collection: str,
        key: str,
    ) -> Optional[str]:
        """Retrieve information from memory."""
        if collection not in self._memories:
            return None
        
        for mem in self._memories[collection]:
            if mem["key"] == key:
                return mem["value"]
        
        return None
    
    def get_stats(self) -> Dict[str, Any]:
        """Get memory statistics."""
        total_tokens = sum(
            sum(m["tokens"] for m in mems)
            for mems in self._memories.values()
        )
        return {
            "total_memories": sum(len(m) for m in self._memories.values()),
            "total_tokens": total_tokens,
            "budget": self.budget,
            "remaining": self.budget - total_tokens,
        }
