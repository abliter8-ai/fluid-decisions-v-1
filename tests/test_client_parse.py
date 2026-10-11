import unittest

from fluid.client import ClientError, enrich_response, validate_response


class ClientParseTest(unittest.TestCase):
    def setUp(self):
        self.questions = {
            "decision": {"type": "choice", "criteria": {"billing": "Billing", "support": "Support"}},
            "severity": {"type": "score", "criteria": ["low", "medium", "high"]},
            "safe": {"type": "noul"},
        }
        self.response = {
            "answers": {
                "decision": {"type": "choice", "choice": "billing", "probabilities": {"billing": 0.75, "support": 0.25}, "confidence": 0.5},
                "severity": {"type": "score", "score": 0.5, "legend": {"0": "low", "1": "medium", "2": "high"}, "probabilities": {"0": 0.2, "1": 0.7, "2": 0.1}, "confidence": 0.6},
                "safe": {"type": "noul", "noul": 0.25},
            },
            "usage": {"input_tokens": 10, "output_tokens": 0},
        }

    def test_validate_and_enrich_preserves_raw_answers(self):
        validate_response(self.response, self.questions)
        enriched = enrich_response(self.response, "max", 12.3, self.questions)
        self.assertEqual(enriched["answers"]["safe"]["noul"], 0.25)
        self.assertEqual(enriched["answers"]["safe"]["decisiveness"], 0.5)
        self.assertEqual(enriched["answers"]["decision"]["top_probability"], 0.75)
        self.assertEqual(enriched["answers"]["severity"]["top_probability"], 0.7)
        self.assertEqual(enriched["lane"], "max")
        self.assertEqual(enriched["timings"]["client_ms"], 12.3)

    def test_invalid_response_is_safe_client_error(self):
        bad = {"answers": {}, "usage": {"output_tokens": 1}}
        with self.assertRaisesRegex(ClientError, "invalid response"):
            validate_response(bad, self.questions)

    def test_invalid_noul_and_distribution_are_rejected(self):
        self.response["answers"]["safe"]["noul"] = 1.2
        with self.assertRaises(ClientError):
            validate_response(self.response, self.questions)

    def test_confidence_and_criteria_metadata_are_strict(self):
        self.response["answers"]["decision"]["confidence"] = float("nan")
        with self.assertRaises(ClientError):
            validate_response(self.response, self.questions)
        self.response["answers"]["decision"]["confidence"] = 0.5
        self.response["answers"]["decision"]["probabilities"]["other"] = 0.0
        with self.assertRaises(ClientError):
            validate_response(self.response, self.questions)
        self.response["answers"]["decision"]["probabilities"].pop("other")
        self.response["answers"]["severity"]["score"] = 3.0
        with self.assertRaises(ClientError):
            validate_response(self.response, self.questions)
        self.response["answers"]["safe"]["noul"] = 0.25
        self.response["answers"]["decision"]["probabilities"]["billing"] = 0.9
        with self.assertRaises(ClientError):
            validate_response(self.response, self.questions)


if __name__ == "__main__":
    unittest.main()
