"""Cluster client regression checks using in-memory HTTP responses (no GPU/network)."""

import asyncio
import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from jinja2 import Environment
from pydantic import BaseModel

from mce.utils import init_embeddings, setup_base_agent_workspace
from mce.workspace_utils import llm as llm_utils


class Analysis(BaseModel):
    pattern: str
    confidence: float


class ClusterRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.env_patch = patch.dict(os.environ, {
            "DASHSCOPE_API_BASE": "http://chat.test:8000/v1",
            "DASHSCOPE_API_KEY": "EMPTY",
            "MCE_MODEL": "llama-3.1-8b",
            "MCE_EMBEDDING_API_BASE": "http://embed.test:8001/v1",
            "MCE_EMBEDDING_API_KEY": "embed-key",
            "MCE_EMBEDDING_MODEL": "qwen-embed",
            "MCE_LLM_STRUCTURED_METHOD": "function_calling",
        })
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        real_async_client = httpx.AsyncClient
        handler = self.respond

        class MockAsyncClient(real_async_client):
            def __init__(self, *args, **kwargs):
                kwargs["transport"] = httpx.MockTransport(handler)
                super().__init__(*args, **kwargs)

        self.http_patch = patch("httpx.AsyncClient", MockAsyncClient)
        self.http_patch.start()
        self.addCleanup(self.http_patch.stop)

    def respond(self, request):
        body = json.loads(request.content)
        self.requests.append((request, body))
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={
                "object": "list",
                "model": "qwen-embed",
                "data": [
                    {"object": "embedding", "index": i, "embedding": [1.0, 0.0]}
                    for i, _ in enumerate(body["input"])
                ],
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
            })
        message = {"role": "assistant", "content": "plain response"}
        if body.get("tools"):
            message = {
                "role": "assistant", "content": None,
                "tool_calls": [{
                    "id": "call_analysis", "type": "function",
                    "function": {
                        "name": body["tools"][0]["function"]["name"],
                        "arguments": json.dumps({"pattern": "fever", "confidence": 0.8}),
                    },
                }],
            }
        return httpx.Response(200, json={
            "id": "chatcmpl-test", "object": "chat.completion", "created": 0,
            "model": "llama-3.1-8b",
            "choices": [{"index": 0, "message": message,
                         "finish_reason": "tool_calls" if body.get("tools") else "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    def test_plain_text_single_and_batch_do_not_request_tools(self):
        self.assertEqual(llm_utils.call_llm("hello"), "plain response")
        self.assertEqual(llm_utils.call_llm(["a", "b"]), ["plain response"] * 2)
        for request, body in self.requests:
            self.assertEqual(request.url.host, "chat.test")
            self.assertEqual(body["model"], "llama-3.1-8b")
            self.assertNotIn("tools", body)
            self.assertNotIn("response_format", body)

    def test_sync_api_inside_running_loop_and_repeated_calls(self):
        async def run():
            for _ in range(2):
                self.assertEqual(llm_utils.call_llm("hello"), "plain response")
        asyncio.run(run())

    def test_async_api_without_schema(self):
        self.assertEqual(asyncio.run(llm_utils.call_llm_async(["hello"])), ["plain response"])

    def test_schema_uses_explicit_function_calling(self):
        result = llm_utils.call_llm("analyze", schema=Analysis)
        self.assertIsInstance(result, Analysis)
        self.assertEqual(result.pattern, "fever")
        body = self.requests[0][1]
        self.assertEqual(body["tool_choice"]["function"]["name"], "Analysis")
        self.assertNotIn("response_format", body)

    def test_invalid_schema_and_method_fail_before_request(self):
        with self.assertRaises(TypeError):
            llm_utils.call_llm("hello", schema=dict)
        with patch.dict(os.environ, {"MCE_LLM_STRUCTURED_METHOD": "unsupported"}):
            with self.assertRaises(ValueError):
                llm_utils.call_llm("hello", schema=Analysis)
        self.assertEqual(self.requests, [])

    def test_embedding_dedicated_endpoint_sends_text_and_float(self):
        client = httpx.Client(transport=httpx.MockTransport(self.respond))
        with client:
            embeddings = init_embeddings(http_client=client)
            self.assertEqual(embeddings.embed_documents(["患者症状"]), [[1.0, 0.0]])
        request, body = self.requests[0]
        self.assertEqual(request.url.host, "embed.test")
        self.assertEqual(request.headers["authorization"], "Bearer embed-key")
        self.assertEqual(body["input"], ["患者症状"])
        self.assertEqual(body["encoding_format"], "float")
        self.assertEqual(body["model"], "qwen-embed")

    def test_embedding_legacy_endpoint_fallback(self):
        with patch.dict(os.environ, {"MCE_EMBEDDING_API_BASE": "", "MCE_EMBEDDING_API_KEY": ""}):
            client = httpx.Client(transport=httpx.MockTransport(self.respond))
            with client:
                init_embeddings(http_client=client).embed_documents(["hello"])
        request, _ = self.requests[0]
        self.assertEqual(request.url.host, "chat.test")
        self.assertEqual(request.headers["authorization"], "Bearer EMPTY")

    def test_copied_embedding_utility_uses_independent_service(self):
        module = importlib.import_module("mce.workspace_utils.embedding")
        module = importlib.reload(module)
        self.assertEqual(module.embeddings.openai_api_base, "http://embed.test:8001/v1")
        self.assertFalse(module.embeddings.check_embedding_ctx_length)
        self.assertEqual(module.embeddings.model_kwargs["encoding_format"], "float")

    def test_dotenv_preserves_exported_model(self):
        from mce import main, eval as evaluation
        importlib.reload(main)
        importlib.reload(evaluation)
        self.assertEqual(os.environ["DASHSCOPE_API_BASE"], "http://chat.test:8000/v1")
        self.assertEqual(os.environ["MCE_MODEL"], "llama-3.1-8b")

    def test_custom_workspace_depth_copies_utilities(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "arbitrary" / "deep" / "output"
            iteration = root / "iter1_sub0"
            iteration.mkdir(parents=True)
            setup_base_agent_workspace(root, iteration, 1, env=None)
            self.assertTrue((iteration / "utils" / "llm.py").is_file())
            self.assertTrue((iteration / "utils" / "embedding.py").is_file())

    def test_template_renders_tool_call_and_result(self):
        path = Path(__file__).resolve().parents[1] / "assets" / "tool_chat_template_llama3.1_json.jinja"
        template = Environment().from_string(path.read_text())
        rendered = template.render(
            bos_token="<|begin_of_text|>", add_generation_prompt=True,
            tools=[{"type": "function", "function": {"name": "Read", "parameters": {}}}],
            messages=[
                {"role": "system", "content": "Use tools"},
                {"role": "user", "content": "Read a file"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "function": {"name": "Read", "arguments": {"file_path": "data.json"}},
                }]},
                {"role": "tool", "content": "file content"},
            ],
        )
        self.assertIn('"name": "Read"', rendered)
        self.assertIn('"file_path": "data.json"', rendered)
        self.assertIn('<|start_header_id|>ipython', rendered)
        self.assertIn('file content', rendered)


if __name__ == "__main__":
    unittest.main()
