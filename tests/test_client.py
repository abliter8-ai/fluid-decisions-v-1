import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from fluid.client import ClientError, D1Client


def response_body(question="safe"):
    return {"answers": {question: {"type": "noul", "noul": 0.25}}, "usage": {"output_tokens": 0}}


class ClientTest(unittest.TestCase):
    def test_post_result_budget_uses_measured_model_and_policy_margin(self):
        with D1Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response_body()))) as client:
            client.cost_models['max'] = {'intercept_ms': 100.0, 'ms_per_char': 0.05}
            budget = client.post_result_budget(12000, {'budget_margin': 2.0, 'max_budget_s': 30})
        self.assertEqual(budget['budget_source'], 'measured')
        self.assertEqual(budget['expected_ms'], 700.0)
        self.assertEqual(budget['budget_s'], 1.4)
        self.assertFalse(budget['exceeds_budget'])

    def test_post_result_budget_reports_cap_overrun_without_sending(self):
        with D1Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response_body()))) as client:
            client.cost_models['max'] = {'intercept_ms': 100.0, 'ms_per_char': 0.05}
            budget = client.post_result_budget(12000, {'budget_margin': 2.0, 'max_budget_s': 1})
        self.assertTrue(budget['exceeds_budget'])
        self.assertEqual(budget['budget_s'], 1.0)

    def test_post_result_budget_override_and_missing_model_fallback(self):
        with mock.patch.dict(os.environ, {'FD_POST_TIMEOUT': '4'}, clear=True):
            with D1Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response_body()))) as client:
                client.cost_models['max'] = {'intercept_ms': 100.0, 'ms_per_char': 0.05}
                overridden = client.post_result_budget(12000, {'budget_margin': 2.0, 'max_budget_s': 3})
                client.cost_models.clear()
                fallback = client.post_result_budget(12000, {'budget_margin': 2.0, 'max_budget_s': 3})
        self.assertEqual(overridden['budget_source'], 'override')
        self.assertEqual(overridden['budget_s'], 3.0)
        self.assertIsNotNone(overridden['expected_ms'])
        self.assertEqual(fallback['budget_source'], 'override')
        self.assertIsNone(fallback['expected_ms'])
        self.assertEqual(fallback['budget_s'], 3.0)

    def test_warmup_probes_post_result_shapes_and_records_model(self):
        calls = []

        def handler(request):
            calls.append(request)
            import json
            body = json.loads(request.content) if request.content else {}
            if request.url.path.endswith('/health'):
                return httpx.Response(200, json={'status': 'ok'})
            if request.url.path.endswith('/batch'):
                answers = {
                    'directed_injection': {'type': 'noul', 'noul': 0.1},
                    'kind': {'type': 'choice', 'choice': 'benign',
                             'probabilities': {'benign': 1.0, 'injection': 0.0, 'canary': 0.0},
                             'confidence': 1.0},
                }
                return httpx.Response(200, json={'results': [{'answers': answers, 'usage': {'output_tokens': 0}}], 'timings': {}})
            question = next(iter(body['questions']))
            return httpx.Response(200, json=response_body(question))

        with D1Client(transport=httpx.MockTransport(handler)) as client:
            from fluid.policy import load_pack
            pack = load_pack('gate-packet')
            pack['post_result']['policy']['warmup_probe_fraction'] = 0.25
            with mock.patch('fluid.policy.load_pack', return_value=pack):
                client.warmup('max')
            health = client.health()
        import json
        batches = [json.loads(request.content)['requests'][0] for request in calls if request.url.path.endswith('/batch')]
        # Two post-result probes (full and quarter window) plus one quarter-window
        # gate-question probe that isolates the marginal per-question cost.
        self.assertEqual([len(item['state']) for item in batches], [12000, 3000, 3000])
        self.assertEqual(list(batches[0]['questions']), ['directed_injection', 'kind'])
        self.assertEqual(len(batches[2]['questions']), 12)
        self.assertIn('max', health['cost_models'])
        self.assertIn('ms_per_question', health['cost_models']['max'])
        self.assertIsNotNone(health['last_warmup_ms'])

    def test_warmup_probes_units_pack_with_explicit_probe_chars(self):
        calls = []

        def handler(request):
            calls.append(request)
            import json
            if request.url.path.endswith('/health'):
                return httpx.Response(200, json={'status': 'ok'})
            if request.url.path.endswith('/batch'):
                body = json.loads(request.content)

                def answer_for(question):
                    kind = question.get('type')
                    if kind == 'score':
                        count = len(question.get('criteria', []))
                        return {'type': 'score', 'score': 0, 'confidence': 1.0,
                                'probabilities': {str(i): (1.0 if i == 0 else 0.0) for i in range(count)}}
                    if kind == 'choice':
                        keys = list(question.get('criteria', {})) or ['benign']
                        return {'type': 'choice', 'choice': keys[0], 'confidence': 1.0,
                                'probabilities': {k: (1.0 if k == keys[0] else 0.0) for k in keys}}
                    return {'type': 'noul', 'noul': 0.1}

                answers = {name: answer_for(q) for name, q in body['requests'][0]['questions'].items()}
                return httpx.Response(200, json={'results': [{'answers': answers, 'usage': {'output_tokens': 0}}],
                                                 'timings': {}})
            warm = json.loads(request.content) if request.content else {}
            return httpx.Response(200, json=response_body(next(iter(warm.get('questions', {'safe': 1})))))

        with D1Client(transport=httpx.MockTransport(handler)) as client:
            with mock.patch.dict(os.environ, {'FD_GATE_PACK': 'gate'}):
                client.warmup('max')
                health = client.health()
        import json
        batches = [json.loads(request.content)['requests'][0] for request in calls if request.url.path.endswith('/batch')]
        # Probe lengths come from warmup_probe_chars; the gate probe isolates the
        # per-question term using the flattened facts questions.
        self.assertEqual([len(item['state']) for item in batches], [12000, 3000, 3000])
        self.assertEqual(list(batches[0]['questions']), ['addressed_to_agent', 'beyond_task'])
        # Nine axis facts plus the carries_out instruction question (IP-007 §3).
        self.assertEqual(len(batches[2]['questions']), 11)
        self.assertIn('ms_per_question', health['cost_models']['max'])

    def test_invalid_post_timeout_override_is_rejected(self):
        for value in ('0', 'nan', 'inf'):
            with self.subTest(value=value), mock.patch.dict(os.environ, {'FD_POST_TIMEOUT': value}, clear=True):
                with self.assertRaises(ValueError):
                    D1Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response_body())))

    def test_routes_media_and_rejects_mixed_before_http(self):
        seen = []

        def handler(request):
            seen.append(request.url.path)
            return httpx.Response(200, json=response_body())

        questions = {"safe": {"type": "noul", "instructions": "?"}}
        with D1Client(transport=httpx.MockTransport(handler)) as client:
            client.system_one("x", questions)
            client.system_one("x", questions, audio="data:audio/wav;base64,AA==")
            with self.assertRaises(ClientError):
                client.system_one("x", questions, images=["data:image/png;base64,AA=="], audio="data:audio/wav;base64,AA==")
        self.assertEqual(seen, ["/v1/systemone", "/v1/systemone"])

    def test_http_error_and_timeout_have_safe_reasons(self):
        def error_handler(request):
            return httpx.Response(503, json={"error": "secret state should not leak"})

        questions = {"safe": {"type": "noul", "instructions": "?"}}
        with D1Client(transport=httpx.MockTransport(error_handler)) as client:
            with self.assertRaises(ClientError) as caught:
                client.system_one("x", questions)
        self.assertEqual(caught.exception.reason, "http_error")
        self.assertNotIn("secret", str(caught.exception))

    def test_batch_rejects_item_with_image_and_audio_before_http(self):
        called = []

        def handler(request):
            called.append(request)
            return httpx.Response(200, json={"results": []})

        request = {"state": "x", "questions": {"safe": {"type": "noul", "instructions": "?"}}, "images": ["x"], "audio": "y"}
        with D1Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(ClientError):
                client.system_one_batch([request], lane="omni")
        self.assertEqual(called, [])

    def test_env_urls_override_defaults(self):
        with mock.patch.dict(os.environ, {"FD_MAX_URL": "http://max.test", "FD_OMNI_URL": "http://omni.test"}, clear=True):
            client = D1Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "ok"})))
            self.assertEqual(client.urls["max"], "http://max.test")
            self.assertEqual(client.urls["omni"], "http://omni.test")
            client.close()

    def test_health_does_not_consume_inference_first_timeout(self):
        def handler(request):
            if request.url.path == "/health":
                return httpx.Response(200, json={"status": "ok"})
            return httpx.Response(200, json=response_body())

        with D1Client(transport=httpx.MockTransport(handler)) as client:
            client.health()
            self.assertEqual(client._seen, set())

    def test_missing_omni_is_disabled_and_malformed_batch_is_rejected_before_http(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "lanes.yaml"
            config.write_text("lanes:\n  max:\n    url: http://127.0.0.1:8774\n  omni: null\n")
            called = []

            def handler(request):
                called.append(request)
                return httpx.Response(200, json=response_body())

            with D1Client(lanes_file=config, transport=httpx.MockTransport(handler)) as client:
                self.assertNotIn("omni", client.urls)
                with self.assertRaises(ClientError) as caught:
                    client.system_one("x", {"safe": {"type": "noul", "instructions": "?"}}, audio="x")
                self.assertEqual(caught.exception.reason, "lane_down")
                with self.assertRaises(ClientError):
                    client.system_one_batch([None])
            self.assertEqual(len(called), 0)


if __name__ == "__main__":
    unittest.main()
