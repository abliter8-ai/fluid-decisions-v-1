import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import probe_contract


class ProbeHelpersTest(unittest.TestCase):
    def test_defaults_are_local_and_environment_overrides_are_supported(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(probe_contract.lane_urls(), {
                "max": "http://127.0.0.1:8774",
                "omni": "http://127.0.0.1:8775",
            })
        with mock.patch.dict(os.environ, {
            "FD_MAX_URL": "http://max.example:9000/",
            "FD_OMNI_URL": "http://omni.example:9001/",
        }, clear=True):
            self.assertEqual(probe_contract.lane_urls(), {
                "max": "http://max.example:9000",
                "omni": "http://omni.example:9001",
            })

    def test_shared_request_contains_all_typed_questions(self):
        request = probe_contract.shared_request()
        self.assertEqual(set(request["questions"]), {"decision", "severity", "safe"})
        self.assertEqual(request["questions"]["decision"]["type"], "choice")
        self.assertEqual(request["questions"]["severity"]["type"], "score")
        self.assertEqual(request["questions"]["safe"]["type"], "noul")
        self.assertEqual(request["model"], "d1")

    def test_encode_media_is_data_url(self):
        encoded = probe_contract.data_url(b"abc", "image/png")
        self.assertEqual(encoded, "data:image/png;base64," + base64.b64encode(b"abc").decode())

    def test_generated_audio_is_one_second_16khz_mono_wav(self):
        wav = probe_contract.audio_wav()
        self.assertEqual(wav[:4], b"RIFF")
        self.assertEqual(wav[8:12], b"WAVE")
        self.assertEqual(int.from_bytes(wav[22:24], "little"), 1)
        self.assertEqual(int.from_bytes(wav[24:28], "little"), 16000)
        self.assertEqual(int.from_bytes(wav[40:44], "little"), 32000)

    def test_validate_success_requires_zero_output_tokens_and_noul(self):
        response = {
            "answers": {"safe": {"type": "noul", "noul": 0.75}},
            "usage": {"output_tokens": 0},
        }
        self.assertEqual(probe_contract.validate_typed_response(response, {"safe"}), [])
        response["usage"]["output_tokens"] = 1
        self.assertTrue(any("output_tokens" in error for error in probe_contract.validate_typed_response(response, {"safe"})))

    def test_validate_typed_response_checks_types_probabilities_and_finite_noul(self):
        response = {
            "answers": {
                "decision": {"type": "choice", "choice": "billing", "probabilities": {"billing": 0.7, "support": 0.3}},
                "severity": {"type": "score", "score": 0.4, "probabilities": {"0": 0.6, "1": 0.4}},
                "safe": {"type": "noul", "noul": 0.5},
            },
            "usage": {"output_tokens": 0},
        }
        self.assertEqual(probe_contract.validate_typed_response(response, {"decision", "severity", "safe"}), [])
        response["answers"]["safe"]["noul"] = float("nan")
        self.assertTrue(any("finite" in error for error in probe_contract.validate_typed_response(response, {"decision", "severity", "safe"})))

    def test_required_result_rejects_transport_and_non_200_status(self):
        self.assertTrue(probe_contract.required_result_errors({"status": None, "response": None}))
        self.assertTrue(probe_contract.required_result_errors({"status": 500, "response": {}}))
        self.assertEqual(probe_contract.required_result_errors({"status": 200, "response": {}}), [])

    def test_typed_result_validates_mocked_http_200_path(self):
        response = {
            "answers": {
                "decision": {"type": "choice", "choice": "billing", "probabilities": {"billing": 0.7, "support": 0.3}},
                "severity": {"type": "score", "score": 0.4, "probabilities": {"0": 0.6, "1": 0.4}},
                "safe": {"type": "noul", "noul": 0.5},
            },
            "usage": {"output_tokens": 0},
        }
        with mock.patch.object(probe_contract, "request_json", return_value={"status": 200, "response": response}):
            result = probe_contract._typed_result("http://lane", probe_contract.shared_request(), 1.0)
        self.assertEqual(result["validation_errors"], [])

    def test_report_has_utc_timestamp_and_redacts_private_checkpoint(self):
        document = probe_contract.new_document({"max": "http://max", "omni": "http://omni"})
        self.assertTrue(document["generated_at"].endswith("Z"))
        # Build the private path from the running user's home so the fixture
        # carries no hard-coded username and still exercises the redaction.
        health = {"status": 200, "response": {"checkpoint": str(Path.home() / "models" / "private")}}
        self.assertEqual(probe_contract.sanitize_health(health)["response"]["checkpoint"], "[redacted]")

    def test_result_document_keeps_raw_response_and_lane_difference(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "probe.json"
            document = probe_contract.new_document({"max": "http://max", "omni": "http://omni"})
            document["lanes"]["max"]["shared"] = [{"status": 200, "response": {"answers": {"safe": {"noul": 0.99}}}}]
            document["lanes"]["omni"]["shared"] = [{"status": 200, "response": {"answers": {"safe": {"noul": 0.64}}}}]
            probe_contract.add_lane_differences(document)
            probe_contract.write_json(target, document)
            loaded = json.loads(target.read_text())
            self.assertEqual(loaded["lanes"]["max"]["shared"][0]["response"]["answers"]["safe"]["noul"], 0.99)
            self.assertTrue(loaded["lane_differences"])


if __name__ == "__main__":
    unittest.main()
