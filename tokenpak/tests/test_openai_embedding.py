"""Unit tests for OpenAIEmbeddingAdapter."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from proxy.adapters.canonical import CanonicalEmbeddingRequest
from proxy.adapters.openai_embedding import OpenAIEmbeddingAdapter


def _make_canonical(**kwargs) -> CanonicalEmbeddingRequest:
    """Return a CanonicalEmbeddingRequest with sensible defaults."""
    defaults = dict(
        model="text-embedding-3-small",
        input=["hello world"],
        encoding_format="float",
        truncate=True,
        normalized=False,
        raw_extra={},
    )
    defaults.update(kwargs)
    return CanonicalEmbeddingRequest(**defaults)


class TestOpenAIEmbeddingAdapterInit(unittest.TestCase):
    """Test adapter class-level properties."""

    def setUp(self):
        self.adapter = OpenAIEmbeddingAdapter()

    def test_source_format(self):
        self.assertEqual(self.adapter.source_format, "openai-embeddings")

    def test_get_env_key_name(self):
        self.assertEqual(self.adapter.get_env_key_name(), "OPENAI_API_KEY")

    def test_get_default_model(self):
        self.assertEqual(self.adapter.get_default_model(), "text-embedding-3-small")

    def test_get_default_upstream(self):
        self.assertEqual(self.adapter.get_default_upstream(), "https://api.openai.com")

    def test_is_available_with_key(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            self.assertTrue(self.adapter.is_available())

    def test_is_available_without_key(self):
        env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(self.adapter.is_available())


class TestOpenAIEmbeddingAdapterDetect(unittest.TestCase):
    """Test the detect() method."""

    def setUp(self):
        self.adapter = OpenAIEmbeddingAdapter()

    def _body(self, model: str) -> bytes:
        return json.dumps({"model": model, "input": ["test"]}).encode()

    def test_detect_text_embedding_3_small(self):
        self.assertTrue(
            self.adapter.detect("/v1/embeddings", {}, self._body("text-embedding-3-small"))
        )

    def test_detect_text_embedding_3_large(self):
        self.assertTrue(
            self.adapter.detect("/v1/embeddings", {}, self._body("text-embedding-3-large"))
        )

    def test_detect_text_embedding_ada_002(self):
        self.assertTrue(
            self.adapter.detect("/v1/embeddings", {}, self._body("text-embedding-ada-002"))
        )

    def test_detect_false_for_jina_model(self):
        self.assertFalse(
            self.adapter.detect("/v1/embeddings", {}, self._body("jina-embeddings-v3"))
        )

    def test_detect_false_for_voyage_model(self):
        self.assertFalse(self.adapter.detect("/v1/embeddings", {}, self._body("voyage-3")))

    def test_detect_false_for_empty_model(self):
        body = json.dumps({"model": "", "input": ["test"]}).encode()
        self.assertFalse(self.adapter.detect("/v1/embeddings", {}, body))

    def test_detect_false_for_none_body(self):
        self.assertFalse(self.adapter.detect("/v1/embeddings", {}, None))

    def test_detect_false_for_invalid_json(self):
        self.assertFalse(self.adapter.detect("/v1/embeddings", {}, b"not json {{"))

    def test_detect_no_path_only_model_in_body(self):
        """detect() should not require any specific path — body model is sufficient."""
        body = self._body("text-embedding-3-large")
        self.assertTrue(self.adapter.detect("/proxy/any/path", {}, body))


class TestOpenAIEmbeddingAdapterNormalizeRequest(unittest.TestCase):
    """Test normalize_request() — near-identity transform of canonical format."""

    def setUp(self):
        self.adapter = OpenAIEmbeddingAdapter()

    def _call(self, canonical: CanonicalEmbeddingRequest):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-key"}):
            return self.adapter.normalize_request(canonical)

    def test_returns_tuple_of_three(self):
        url, headers, body = self._call(_make_canonical())
        self.assertIsInstance(url, str)
        self.assertIsInstance(headers, dict)
        self.assertIsInstance(body, bytes)

    def test_url(self):
        url, _, _ = self._call(_make_canonical())
        self.assertEqual(url, "https://api.openai.com/v1/embeddings")

    def test_auth_header_uses_env_key(self):
        _, headers, _ = self._call(_make_canonical())
        self.assertEqual(headers["Authorization"], "Bearer sk-test-key")

    def test_content_type_header(self):
        _, headers, _ = self._call(_make_canonical())
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_payload_core_fields(self):
        canonical = _make_canonical(
            model="text-embedding-3-large",
            input=["embed this text"],
            encoding_format="base64",
        )
        _, _, body = self._call(canonical)
        payload = json.loads(body)
        self.assertEqual(payload["model"], "text-embedding-3-large")
        self.assertEqual(payload["input"], ["embed this text"])
        self.assertEqual(payload["encoding_format"], "base64")

    def test_string_input_is_wrapped_in_list(self):
        """If canonical.input is a plain string, it must be wrapped in a list."""
        canonical = _make_canonical(input="single string")
        _, _, body = self._call(canonical)
        payload = json.loads(body)
        self.assertEqual(payload["input"], ["single string"])

    def test_list_input_preserved(self):
        canonical = _make_canonical(input=["a", "b", "c"])
        _, _, body = self._call(canonical)
        payload = json.loads(body)
        self.assertEqual(payload["input"], ["a", "b", "c"])

    def test_dimensions_included_when_set(self):
        canonical = _make_canonical(dimensions=512)
        _, _, body = self._call(canonical)
        payload = json.loads(body)
        self.assertEqual(payload["dimensions"], 512)

    def test_dimensions_omitted_when_none(self):
        canonical = _make_canonical(dimensions=None)
        _, _, body = self._call(canonical)
        payload = json.loads(body)
        self.assertNotIn("dimensions", payload)

    def test_raw_extra_preserved(self):
        canonical = _make_canonical(raw_extra={"user": "user-abc"})
        _, _, body = self._call(canonical)
        payload = json.loads(body)
        self.assertEqual(payload["user"], "user-abc")

    def test_raw_extra_drops_input_type(self):
        """input_type is provider-specific and must be stripped from the payload."""
        canonical = _make_canonical(raw_extra={"input_type": "query"})
        _, _, body = self._call(canonical)
        payload = json.loads(body)
        self.assertNotIn("input_type", payload)

    def test_raw_extra_drops_task(self):
        canonical = _make_canonical(raw_extra={"task": "retrieval.query"})
        _, _, body = self._call(canonical)
        payload = json.loads(body)
        self.assertNotIn("task", payload)

    def test_raw_extra_drops_normalized(self):
        canonical = _make_canonical(raw_extra={"normalized": True})
        _, _, body = self._call(canonical)
        payload = json.loads(body)
        self.assertNotIn("normalized", payload)

    def test_raw_extra_does_not_override_core_keys(self):
        """raw_extra cannot override model or input (setdefault semantics)."""
        canonical = _make_canonical(
            model="text-embedding-3-small",
            raw_extra={"model": "evil-model", "input": ["hijacked"]},
        )
        _, _, body = self._call(canonical)
        payload = json.loads(body)
        self.assertEqual(payload["model"], "text-embedding-3-small")

    def test_empty_api_key(self):
        env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            _, headers, _ = self.adapter.normalize_request(_make_canonical())
        self.assertEqual(headers["Authorization"], "Bearer ")


class TestOpenAIEmbeddingAdapterNormalizeResponse(unittest.TestCase):
    """Test normalize_response() — should be a passthrough."""

    def setUp(self):
        self.adapter = OpenAIEmbeddingAdapter()

    def test_passthrough_returns_body_unchanged(self):
        body = json.dumps(
            {
                "object": "list",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                "model": "text-embedding-3-small",
                "usage": {"prompt_tokens": 5, "total_tokens": 5},
            }
        ).encode()
        result = self.adapter.normalize_response(200, {}, body)
        self.assertEqual(result, body)

    def test_passthrough_non_200_status(self):
        body = json.dumps({"error": {"message": "invalid request"}}).encode()
        result = self.adapter.normalize_response(400, {}, body)
        self.assertEqual(result, body)

    def test_passthrough_empty_body(self):
        result = self.adapter.normalize_response(200, {}, b"")
        self.assertEqual(result, b"")

    def test_passthrough_returns_bytes(self):
        body = b'{"data": []}'
        result = self.adapter.normalize_response(200, {}, body)
        self.assertIsInstance(result, bytes)


if __name__ == "__main__":
    unittest.main()
