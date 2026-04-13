"""Unit tests for JinaEmbeddingAdapter."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from proxy.adapters.canonical import CanonicalEmbeddingRequest
from proxy.adapters.jina_embedding import JinaEmbeddingAdapter


def _make_canonical(**kwargs) -> CanonicalEmbeddingRequest:
    """Return a CanonicalEmbeddingRequest with sensible defaults."""
    defaults = dict(
        model="jina-embeddings-v3",
        input=["hello world"],
        encoding_format="float",
        truncate=True,
        normalized=False,
        raw_extra={},
    )
    defaults.update(kwargs)
    return CanonicalEmbeddingRequest(**defaults)


class TestJinaEmbeddingAdapterInit(unittest.TestCase):
    """Test adapter class-level properties."""

    def setUp(self):
        self.adapter = JinaEmbeddingAdapter()

    def test_source_format(self):
        self.assertEqual(self.adapter.source_format, "jina-embeddings")

    def test_get_env_key_name(self):
        self.assertEqual(self.adapter.get_env_key_name(), "JINA_API_KEY")

    def test_get_default_model(self):
        self.assertEqual(self.adapter.get_default_model(), "jina-embeddings-v3")

    def test_get_default_upstream(self):
        self.assertEqual(self.adapter.get_default_upstream(), "https://api.jina.ai")

    def test_is_available_with_key(self):
        with patch.dict(os.environ, {"JINA_API_KEY": "jina_test_key"}):
            self.assertTrue(self.adapter.is_available())

    def test_is_available_without_key(self):
        env = {k: v for k, v in os.environ.items() if k != "JINA_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(self.adapter.is_available())


class TestJinaEmbeddingAdapterDetect(unittest.TestCase):
    """Test the detect() method."""

    def setUp(self):
        self.adapter = JinaEmbeddingAdapter()

    def _body(self, model: str) -> bytes:
        return json.dumps({"model": model, "input": ["test"]}).encode()

    def test_detect_jina_model_prefix(self):
        body = self._body("jina-embeddings-v3")
        self.assertTrue(self.adapter.detect("/v1/embeddings", {}, body))

    def test_detect_jina_model_v2(self):
        body = self._body("jina-embeddings-v2-base-en")
        self.assertTrue(self.adapter.detect("/v1/embeddings", {}, body))

    def test_detect_false_for_openai_model(self):
        body = self._body("text-embedding-3-small")
        self.assertFalse(self.adapter.detect("/v1/embeddings", {}, body))

    def test_detect_false_for_non_jina_model(self):
        body = self._body("voyage-3")
        self.assertFalse(self.adapter.detect("/v1/embeddings", {}, body))

    def test_detect_path_plus_jina_auth_header(self):
        """Path /v1/embeddings with Authorization: Bearer jina_… should match."""
        headers = {"Authorization": "Bearer jina_abc123"}
        self.assertTrue(self.adapter.detect("/v1/embeddings", headers, None))

    def test_detect_path_lowercase_auth_header(self):
        headers = {"authorization": "Bearer jina_abc123"}
        self.assertTrue(self.adapter.detect("/v1/embeddings", headers, None))

    def test_detect_path_without_jina_auth(self):
        """Path alone (no jina_ in auth) should not match."""
        headers = {"Authorization": "Bearer sk-other"}
        self.assertFalse(self.adapter.detect("/v1/embeddings", headers, None))

    def test_detect_no_body_no_headers(self):
        self.assertFalse(self.adapter.detect("/v1/embeddings", {}, None))

    def test_detect_invalid_json_body_falls_through(self):
        """Malformed body should not crash; path+auth detection still works."""
        headers = {"Authorization": "Bearer jina_key"}
        bad_body = b"not json {{"
        self.assertTrue(self.adapter.detect("/v1/embeddings", headers, bad_body))

    def test_detect_invalid_json_no_fallback(self):
        """Malformed body with no matching path/auth should return False."""
        bad_body = b"not json {{"
        self.assertFalse(self.adapter.detect("/other/path", {}, bad_body))


class TestJinaEmbeddingAdapterNormalizeRequest(unittest.TestCase):
    """Test normalize_request() field mapping and output format."""

    def setUp(self):
        self.adapter = JinaEmbeddingAdapter()

    def _call(self, canonical: CanonicalEmbeddingRequest):
        with patch.dict(os.environ, {"JINA_API_KEY": "jina_test_key"}):
            return self.adapter.normalize_request(canonical)

    def test_returns_tuple_of_three(self):
        url, headers, body = self._call(_make_canonical())
        self.assertIsInstance(url, str)
        self.assertIsInstance(headers, dict)
        self.assertIsInstance(body, bytes)

    def test_url(self):
        url, _, _ = self._call(_make_canonical())
        self.assertEqual(url, "https://api.jina.ai/v1/embeddings")

    def test_auth_header_uses_env_key(self):
        _, headers, _ = self._call(_make_canonical())
        self.assertEqual(headers["Authorization"], "Bearer jina_test_key")

    def test_content_type_header(self):
        _, headers, _ = self._call(_make_canonical())
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_payload_core_fields(self):
        canonical = _make_canonical(
            model="jina-embeddings-v3",
            input=["embed me"],
            encoding_format="base64",
            truncate=False,
            normalized=True,
        )
        _, _, body = self._call(canonical)
        payload = json.loads(body)
        self.assertEqual(payload["model"], "jina-embeddings-v3")
        self.assertEqual(payload["input"], ["embed me"])
        self.assertEqual(payload["embedding_type"], "base64")
        self.assertFalse(payload["truncate"])
        self.assertTrue(payload["normalized"])

    def test_input_type_query_maps_to_retrieval_query(self):
        canonical = _make_canonical(input_type="query")
        _, _, body = self._call(canonical)
        payload = json.loads(body)
        self.assertEqual(payload["task"], "retrieval.query")

    def test_input_type_document_maps_to_retrieval_passage(self):
        canonical = _make_canonical(input_type="document")
        _, _, body = self._call(canonical)
        payload = json.loads(body)
        self.assertEqual(payload["task"], "retrieval.passage")

    def test_input_type_text_matching(self):
        canonical = _make_canonical(input_type="text-matching")
        _, _, body = self._call(canonical)
        self.assertEqual(json.loads(body)["task"], "text-matching")

    def test_input_type_classification(self):
        canonical = _make_canonical(input_type="classification")
        _, _, body = self._call(canonical)
        self.assertEqual(json.loads(body)["task"], "classification")

    def test_input_type_separation(self):
        canonical = _make_canonical(input_type="separation")
        _, _, body = self._call(canonical)
        self.assertEqual(json.loads(body)["task"], "separation")

    def test_input_type_unknown_passed_through(self):
        canonical = _make_canonical(input_type="some-future-type")
        _, _, body = self._call(canonical)
        self.assertEqual(json.loads(body)["task"], "some-future-type")

    def test_task_used_when_input_type_is_none(self):
        """When input_type is None and task is set, task is forwarded directly."""
        canonical = _make_canonical(input_type=None, task="retrieval.passage")
        _, _, body = self._call(canonical)
        self.assertEqual(json.loads(body)["task"], "retrieval.passage")

    def test_no_task_field_when_both_none(self):
        canonical = _make_canonical(input_type=None, task=None)
        _, _, body = self._call(canonical)
        payload = json.loads(body)
        self.assertNotIn("task", payload)

    def test_raw_extra_fields_are_included(self):
        canonical = _make_canonical(raw_extra={"custom_field": "value"})
        _, _, body = self._call(canonical)
        payload = json.loads(body)
        self.assertEqual(payload["custom_field"], "value")

    def test_dimensions_not_in_payload(self):
        """Jina v1 doesn't support dimensions; it must be silently dropped."""
        canonical = _make_canonical(dimensions=512)
        _, _, body = self._call(canonical)
        payload = json.loads(body)
        self.assertNotIn("dimensions", payload)

    def test_empty_api_key(self):
        """With no key set, Authorization header should be 'Bearer '."""
        env = {k: v for k, v in os.environ.items() if k != "JINA_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            _, headers, _ = self.adapter.normalize_request(_make_canonical())
        self.assertEqual(headers["Authorization"], "Bearer ")


class TestJinaEmbeddingAdapterNormalizeResponse(unittest.TestCase):
    """Test normalize_response() normalisation logic."""

    def setUp(self):
        self.adapter = JinaEmbeddingAdapter()

    def _call(self, data: dict) -> dict:
        body = json.dumps(data).encode()
        result = self.adapter.normalize_response(200, {}, body)
        return json.loads(result)

    def test_injects_object_field_when_missing(self):
        data = {"data": [{"index": 0, "embedding": [0.1, 0.2]}], "usage": {}}
        result = self._call(data)
        self.assertEqual(result["data"][0]["object"], "embedding")

    def test_does_not_override_existing_object_field(self):
        data = {"data": [{"index": 0, "object": "custom", "embedding": [0.1]}], "usage": {}}
        result = self._call(data)
        self.assertEqual(result["data"][0]["object"], "custom")

    def test_mirrors_prompt_tokens_to_total_tokens(self):
        data = {"data": [], "usage": {"prompt_tokens": 7}}
        result = self._call(data)
        self.assertEqual(result["usage"]["total_tokens"], 7)

    def test_does_not_override_existing_total_tokens(self):
        data = {"data": [], "usage": {"prompt_tokens": 7, "total_tokens": 9}}
        result = self._call(data)
        self.assertEqual(result["usage"]["total_tokens"], 9)

    def test_multiple_embeddings_all_get_object(self):
        data = {
            "data": [
                {"index": 0, "embedding": [0.1]},
                {"index": 1, "embedding": [0.2]},
            ],
            "usage": {"prompt_tokens": 4},
        }
        result = self._call(data)
        for item in result["data"]:
            self.assertEqual(item["object"], "embedding")

    def test_missing_usage_block(self):
        """Response with no usage block should not crash."""
        data = {"data": [{"index": 0, "embedding": [0.1]}]}
        result = self._call(data)
        self.assertIn("usage", result)

    def test_output_is_valid_utf8_bytes(self):
        data = {"data": [], "usage": {}}
        body = json.dumps(data).encode()
        result = self.adapter.normalize_response(200, {}, body)
        self.assertIsInstance(result, bytes)
        result.decode("utf-8")  # should not raise


if __name__ == "__main__":
    unittest.main()
