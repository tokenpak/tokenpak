"""No-op passthrough plugin — returns text unchanged."""
from tokenpak.plugins.base import CompressorPlugin


class PassthroughPlugin(CompressorPlugin):
    """Example plugin that returns text unmodified.

    Useful as a template for building real plugins.
    """

    name = "passthrough"

    def compress(self, text: str, context: dict) -> dict:
        return {"text": text, "metadata": {"plugin": self.name, "changed": False}}
