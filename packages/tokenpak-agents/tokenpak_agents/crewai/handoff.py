"""Task handoff with TokenPak for CrewAI."""

from typing import Any, Dict, Optional


class TokenPakHandoff:
    """
    Handles task output → TokenPak → task input handoffs.
    
    Automatically packages task outputs and unpacks them for downstream tasks.
    """
    
    def __init__(
        self,
        format: str = "tokenpak",
        include_metadata: bool = True,
    ):
        self.format = format
        self.include_metadata = include_metadata
    
    def prepare_output(self, task_result: Any) -> Dict[str, Any]:
        """Prepare task output as TokenPak."""
        if isinstance(task_result, str):
            content = task_result
        else:
            content = str(task_result)
        
        return {
            "type": "task_output",
            "content": content,
            "format": self.format,
            "metadata": {
                "include_metadata": self.include_metadata,
            } if self.include_metadata else {},
        }
    
    def prepare_input(self, handoff_data: Dict[str, Any]) -> str:
        """Prepare handoff data as input for next task."""
        content = handoff_data.get("content", "")
        return f"Context from previous task:\n{content}"
