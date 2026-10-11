import unittest

from scripts.smoke_worker import parse_chat_response, redact_url, select_models


class WorkerSmokeTest(unittest.TestCase):
    def test_select_models_only_local_qwen_and_glm_ids(self):
        catalog = {"data": [{"id": "Qwen/Qwen3-32B"}, {"id": "Qwen/Qwen3-NoThink"}, {"id": "Abliter8/GLM-4"}, {"id": "openai/gpt"}]}
        self.assertEqual(select_models(catalog), ["Qwen/Qwen3-NoThink", "Abliter8/GLM-4"])

    def test_parse_tool_call_and_empty_completion(self):
        tool = {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"README.md"}'}}
        parsed = parse_chat_response({"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [tool]}}], "usage": {"completion_tokens": 0, "completion_tokens_details": {"reasoning_tokens": 12}}})
        self.assertEqual(parsed["tool_calls"], [tool])
        self.assertEqual(parsed["usage"]["reasoning_tokens"], 12)
        self.assertFalse(parsed["empty_completion"])
        empty = parse_chat_response({"choices": [{"finish_reason": "length", "message": {}}], "usage": {"completion_tokens": 0}})
        self.assertTrue(empty["empty_completion"])
        self.assertEqual(empty["finish_reason"], "length")

    def test_redact_url_removes_query_and_credentials(self):
        self.assertEqual(redact_url("https://user:secret@example.test/v1?api_key=secret"), "https://example.test/v1")


if __name__ == "__main__":
    unittest.main()
