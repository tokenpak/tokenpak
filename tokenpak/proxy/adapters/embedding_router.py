"""EmbeddingRouter — discovers available embedding providers at startup and routes requests."""

from __future__ import annotations

import json
import logging
import os
import signal
from typing import Dict, List, Tuple

from .canonical import CanonicalEmbeddingRequest
from .embedding_base import EmbeddingAdapter
from .gemini_embedding_adapter import GeminiEmbeddingAdapter
from .jina_embedding_adapter import JinaEmbeddingAdapter
from .ollama_embedding_adapter import OllamaEmbeddingAdapter
from .openai_embedding_adapter import OpenAIEmbeddingAdapter
from .voyage_embedding_adapter import VoyageEmbeddingAdapter

logger = logging.getLogger(__name__)


class EmbeddingRouter:
    """Routes embedding requests to the best available provider.

    Priority order (highest first): Voyage > OpenAI > Gemini > Jina > Ollama.
    Provider availability is determined at startup by checking env vars via
    each adapter's is_available() method.  Re-discovery is triggered on SIGHUP.
    """

    def __init__(self) -> None:
        # All adapters in fixed priority order — do not reorder
        self._all_adapters: List[EmbeddingAdapter] = [
            VoyageEmbeddingAdapter(),
            OpenAIEmbeddingAdapter(),
            GeminiEmbeddingAdapter(),
            JinaEmbeddingAdapter(),
            OllamaEmbeddingAdapter(),
        ]
        self.available_providers: List[EmbeddingAdapter] = []
        self.discover_providers()

        # Re-discover on SIGHUP (proxy reload) — skip on platforms that lack it
        try:
            signal.signal(signal.SIGHUP, self._handle_sighup)
        except (OSError, ValueError):
            pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _handle_sighup(self, signum: int, frame: object) -> None:
        logger.info("EmbeddingRouter: SIGHUP received — re-discovering providers")
        self.discover_providers()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def discover_providers(self) -> List[EmbeddingAdapter]:
        """Check each adapter's is_available() and rebuild the priority list.

        Returns the list of available adapters (also stored as self.available_providers).
        Discovery runs once at startup; call again (or send SIGHUP) to refresh.
        """
        available = [a for a in self._all_adapters if a.is_available()]
        self.available_providers = available
        if available:
            logger.info(
                "EmbeddingRouter: available providers: %s",
                [a.source_format for a in available],
            )
        else:
            logger.warning(
                "EmbeddingRouter: no embedding providers available — "
                "set VOYAGE_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, "
                "JINA_API_KEY, or OLLAMA_HOST to enable a provider"
            )
        return available

    def resolve_model(
        self,
        requested_model: str,
        input_texts: List[str] | None = None,
    ) -> Tuple[str, EmbeddingAdapter]:
        """Resolve a model name to a (resolved_model_string, adapter) pair.

        Rules:
        - ``"auto"``        → best available adapter's default model + that adapter
        - explicit name     → adapter whose detect() matches; error if provider unavailable
        - no match          → ValueError with clear message
        - no providers      → RuntimeError (caller should return HTTP 503)
        """
        if not self.available_providers:
            raise RuntimeError(
                "No embedding providers are available. "
                "Set at least one of VOYAGE_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, "
                "JINA_API_KEY, or OLLAMA_HOST."
            )

        if requested_model == "auto":
            model_name, best = _select_auto_model(
                input_texts or [], self.available_providers
            )
            return model_name, best

        # Build a minimal probe body for detect()
        probe_body = json.dumps({"model": requested_model}).encode("utf-8")

        # Walk _all_adapters (priority order) so the first match wins
        for adapter in self._all_adapters:
            if adapter.detect("", {}, probe_body):
                if not adapter.is_available():
                    raise ValueError(
                        f"Model '{requested_model}' belongs to provider "
                        f"'{adapter.source_format}' but its API key is not configured."
                    )
                return requested_model, adapter

        raise ValueError(
            f"No embedding adapter recognises model '{requested_model}'. "
            f"Available providers: {[a.source_format for a in self.available_providers]}"
        )

    def get_providers_status(self) -> List[Dict]:
        """Return status of all embedding providers.

        Each entry contains: name, available, healthy, default_model, key_set, cooldown_until.
        The EmbeddingRouter has no cooldown mechanism, so healthy mirrors available and
        cooldown_until is always null.
        """
        result = []
        for adapter in self._all_adapters:
            key_set = adapter.is_available()
            result.append({
                "name": adapter.source_format,
                "available": key_set,
                "healthy": key_set,
                "default_model": adapter.get_default_model(),
                "key_set": key_set,
                "cooldown_until": None,
            })
        return result

    def handle_request(
        self,
        path: str,
        headers: Dict[str, str],
        body: bytes,
    ) -> Tuple[str, Dict[str, str], bytes]:
        """Parse, resolve, and normalise an incoming embedding request.

        Returns (url, out_headers, out_body) ready to be forwarded by _proxy_to().

        Raises:
            RuntimeError: No providers available (proxy should return 503).
            ValueError:   Unrecognised or unconfigured model (proxy should return 400/422).
            ValueError:   Malformed JSON request body.
        """
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"Invalid JSON in embedding request: {exc}") from exc

        requested_model = str(data.get("model", "auto")) or "auto"
        input_texts = _normalise_input(data.get("input", []))
        resolved_model, adapter = self.resolve_model(requested_model, input_texts)

        _KNOWN_FIELDS = frozenset(
            ("model", "input", "dimensions", "encoding_format",
             "input_type", "task", "truncate", "normalized")
        )

        canonical = CanonicalEmbeddingRequest(
            model=resolved_model,
            input=input_texts,
            dimensions=data.get("dimensions"),
            encoding_format=data.get("encoding_format", "float"),
            input_type=data.get("input_type"),
            task=data.get("task"),
            truncate=data.get("truncate", True),
            normalized=data.get("normalized", False),
            raw_extra={k: v for k, v in data.items() if k not in _KNOWN_FIELDS},
        )

        return adapter.normalize_request(canonical)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

_CODE_SIGNALS = (
    "def ", "class ", "import ", "from ", "#!/",
    "if [", "function ", "const ", "let ", "{", "}", "=>", "->",
)


def _looks_like_code(text: str) -> bool:
    """Return True if the first 500 chars of *text* contain >=2 code signals."""
    sample = text[:500]
    hits = sum(1 for signal in _CODE_SIGNALS if signal in sample)
    return hits >= 2


def _select_auto_model(
    input_texts: List[str],
    available: List[EmbeddingAdapter],
) -> Tuple[str, EmbeddingAdapter]:
    """Pick the best (model, adapter) for model='auto' requests.

    When TOKENPAK_EMBEDDING_CONTENT_ROUTING=1 and Voyage is the top provider,
    analyse *input_texts*: if >30% look like code, route to voyage-code-3;
    otherwise use voyage-3.5.  Falls back to the standard priority order when
    the env var is unset or Voyage is unavailable.
    """
    best = available[0]
    content_routing = os.environ.get("TOKENPAK_EMBEDDING_CONTENT_ROUTING", "0") == "1"
    voyage_available = best.source_format == "voyage-embeddings"

    if content_routing and voyage_available and input_texts:
        code_count = sum(1 for t in input_texts if _looks_like_code(t))
        ratio = code_count / len(input_texts)
        if ratio > 0.30:
            logger.debug(
                "Content routing: %.0f%% code texts → voyage-code-3", ratio * 100
            )
            return "voyage-code-3", best
        logger.debug(
            "Content routing: %.0f%% code texts → voyage-3.5", ratio * 100
        )
        return "voyage-3.5", best

    return best.get_default_model(), best


def _normalise_input(raw: object) -> list:
    """Return ``raw`` as a list of strings regardless of whether it is a str or list."""
    if isinstance(raw, list):
        return [str(item) for item in raw]
    return [str(raw)]
