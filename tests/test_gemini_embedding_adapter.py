"""Unit tests for GeminiEmbeddingAdapter — batch and single input correctness."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from tokenpak.proxy.adapters.canonical import CanonicalEmbeddingRequest
from tokenpak.proxy.adapters.gemini_embedding_adapter import GeminiEmbeddingAdapter


def _req(**kwargs) -> CanonicalEmbeddingRequest:
    defaults = dict(
        model="gemini-embedding-001",
        input=["hello world"],
        encoding_format="float",
        truncate=True,
        normalized=False,
        raw_extra={},
    )
    defaults.update(kwargs)
    return CanonicalEmbeddingRequest(**defaults)


class TestGeminiSingleInput(unittest.TestCase):
    """Single-input requests must use the embedContent endpoint."""

    def setUp(self):
        self.adapter = GeminiEmbeddingAdapter()

    def _call(self, canonical):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            return self.adapter.normalize_request(canonical)

    def test_single_input_url_uses_embed_content_endpoint(self):
        url, _, _ = self._call(_req(input=["hello"]))
        self.assertIn(":embedContent", url)
        self.assertNotIn("batchEmbed", url)

    def test_single_input_payload_text(self):
        _, _, body = self._call(_req(input=["embed this"]))
        payload = json.loads(body)
        self.assertEqual(payload["content"]["parts"][0]["text"], "embed this")

    def test_single_input_no_requests_key(self):
        _, _, body = self._call(_req(input=["x"]))
        payload = json.loads(body)
        self.assertNotIn("requests", payload)

    def test_single_input_dimensions_mapped(self):
        _, _, body = self._call(_req(input=["x"], dimensions=512))
        payload = json.loads(body)
        self.assertEqual(payload["outputDimensionality"], 512)

    def test_empty_input_uses_embed_content(self):
        url, _, body = self._call(_req(input=[]))
        self.assertIn(":embedContent", url)
        payload = json.loads(body)
        self.assertEqual(payload["content"]["parts"][0]["text"], "")


class TestGeminiBatchInput(unittest.TestCase):
    """Batch requests (>1 input) must use batchEmbedContents with all texts preserved."""

    def setUp(self):
        self.adapter = GeminiEmbeddingAdapter()

    def _call(self, canonical):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            return self.adapter.normalize_request(canonical)

    def test_batch_url_uses_batch_embed_contents_endpoint(self):
        url, _, _ = self._call(_req(input=["a", "b", "c"]))
        self.assertIn(":batchEmbedContents", url)
        self.assertNotIn(":embedContent?", url)

    def test_batch_all_inputs_preserved(self):
        texts = ["first", "second", "third"]
        _, _, body = self._call(_req(input=texts))
        payload = json.loads(body)
        got = [r["content"]["parts"][0]["text"] for r in payload["requests"]]
        self.assertEqual(got, texts)

    def test_batch_request_count_matches_input_count(self):
        _, _, body = self._call(_req(input=["a", "b", "c", "d"]))
        payload = json.loads(body)
        self.assertEqual(len(payload["requests"]), 4)

    def test_batch_each_request_has_model_field(self):
        _, _, body = self._call(_req(input=["x", "y"]))
        payload = json.loads(body)
        for req in payload["requests"]:
            self.assertIn("model", req)
            self.assertIn("gemini-embedding-001", req["model"])

    def test_batch_dimensions_propagated_to_all_requests(self):
        _, _, body = self._call(_req(input=["a", "b"], dimensions=256))
        payload = json.loads(body)
        for req in payload["requests"]:
            self.assertEqual(req["outputDimensionality"], 256)

    def test_batch_two_inputs_minimum(self):
        url, _, body = self._call(_req(input=["one", "two"]))
        self.assertIn(":batchEmbedContents", url)
        payload = json.loads(body)
        self.assertEqual(len(payload["requests"]), 2)


class TestGeminiNormalizeResponseSingle(unittest.TestCase):
    """normalize_response handles embedContent (single) responses."""

    def setUp(self):
        self.adapter = GeminiEmbeddingAdapter()

    def _call(self, data):
        return json.loads(self.adapter.normalize_response(200, {}, json.dumps(data).encode()))

    def test_single_response_data_has_one_item(self):
        result = self._call({"embedding": {"values": [0.1, 0.2]}})
        self.assertEqual(len(result["data"]), 1)

    def test_single_response_embedding_values(self):
        values = [0.1, 0.2, 0.3]
        result = self._call({"embedding": {"values": values}})
        self.assertEqual(result["data"][0]["embedding"], values)

    def test_single_response_index_zero(self):
        result = self._call({"embedding": {"values": [0.1]}})
        self.assertEqual(result["data"][0]["index"], 0)


class TestGeminiNormalizeResponseBatch(unittest.TestCase):
    """normalize_response handles batchEmbedContents responses."""

    def setUp(self):
        self.adapter = GeminiEmbeddingAdapter()

    def _call(self, data):
        return json.loads(self.adapter.normalize_response(200, {}, json.dumps(data).encode()))

    def test_batch_response_data_count_matches_embeddings(self):
        result = self._call({"embeddings": [{"values": [0.1]}, {"values": [0.2]}, {"values": [0.3]}]})
        self.assertEqual(len(result["data"]), 3)

    def test_batch_response_indices_sequential(self):
        result = self._call({"embeddings": [{"values": [0.1]}, {"values": [0.2]}]})
        self.assertEqual(result["data"][0]["index"], 0)
        self.assertEqual(result["data"][1]["index"], 1)

    def test_batch_response_values_preserved(self):
        v1, v2 = [0.1, 0.2], [0.3, 0.4]
        result = self._call({"embeddings": [{"values": v1}, {"values": v2}]})
        self.assertEqual(result["data"][0]["embedding"], v1)
        self.assertEqual(result["data"][1]["embedding"], v2)

    def test_batch_response_object_field_is_list(self):
        result = self._call({"embeddings": [{"values": [0.1]}]})
        self.assertEqual(result["object"], "list")


if __name__ == "__main__":
    unittest.main()
