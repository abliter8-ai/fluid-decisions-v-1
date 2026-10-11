import pytest

from fluid.serving import DecisionService, RequestError


class Fake:
    def system_one(self, state, questions, images=None, audio=None):
        return {
            "answers": {k: {"type": "noul", "noul": 0.5} for k in questions},
            "usage": {"input_tokens": 1, "output_tokens": 0},
        }

    def system_one_batch(self, requests):
        return [self.system_one(*r) for r in requests]


def body():
    return {"state": "x", "questions": {"ok": {"type": "noul", "instructions": "ok"}}}


def test_single_contract():
    result = DecisionService(model=Fake()).invoke(body())
    assert (
        result["model"] == "d1-3B"
        and result["usage"]["output_tokens"] == 0
        and "ms" in result["timings"]
    )


def test_batch_uses_model_batch_contract():
    result = DecisionService(model=Fake()).batch({"requests": [body(), body()]})
    assert len(result["results"]) == 2 and result["model"] == "d1-3B" and "ms" in result["timings"]


def test_mixed_media_rejected_before_model():
    x = body() | {"images": ["AAAA"], "audio": "AAAA"}
    with pytest.raises(RequestError, match="cannot be combined"):
        DecisionService(model=Fake()).invoke(x)


@pytest.mark.parametrize("question", [{"type": "noul"}, {"type": "choice", "instructions": "x"}])
def test_question_shape_required(question):
    with pytest.raises(RequestError):
        DecisionService(model=Fake()).invoke({"questions": {"x": question}})


def test_audio_survives_batch_adapter(monkeypatch):
    from fluid import serving
    decoded = object()
    monkeypatch.setattr(serving, '_audio', lambda _: decoded)
    class AudioModel(Fake):
        def system_one_batch(self, requests):
            assert requests[0][3] is decoded
            return super().system_one_batch(requests)
    assert DecisionService(model=AudioModel(), model_name='d1-omni-600M').invoke(
        body() | {'audio': 'encoded'})['usage']['output_tokens'] == 0


def test_missing_usage_never_invented():
    class MissingUsage(Fake):
        def system_one_batch(self, requests):
            return [{'answers': {}}]
    with pytest.raises(RequestError, match='usage'):
        DecisionService(model=MissingUsage()).invoke(body())


def test_missing_state_and_invalid_choice_rejected():
    service = DecisionService(model=Fake())
    with pytest.raises(RequestError, match='state'):
        service.invoke({'questions': body()['questions']})
    with pytest.raises(RequestError, match='criteria'):
        service.invoke({'state': 'x', 'questions': {'q': {
            'type': 'choice', 'instructions': 'select', 'criteria': {'one': 'one'}}}})
