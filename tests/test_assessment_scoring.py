import asyncio
from types import SimpleNamespace

from app.services import assessment_scoring


class _FakeSession:
    def __init__(self, assessment):
        self._assessment = assessment

    def get(self, *_args, **_kwargs):
        return self._assessment

    def close(self):
        return None


def test_evaluate_assessment_repairs_invalid_json(monkeypatch):
    assessment = SimpleNamespace(assessment_type_code="mbti")
    session = _FakeSession(assessment)
    call_count = {"value": 0}

    monkeypatch.setattr(assessment_scoring, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        assessment_scoring,
        "get_assessment_definition",
        lambda _code: SimpleNamespace(code="mbti"),
    )
    monkeypatch.setattr(
        assessment_scoring,
        "iter_assessment_answers",
        lambda *_args, **_kwargs: [
            {
                "display_label": "Energy Source",
                "item_key": "energy_source",
                "question_text": "How do you recharge after a long week?",
                "answer_text": "I usually reset alone first, then reconnect with people.",
            }
        ],
    )

    async def _fake_responses_create_async(_client, **_kwargs):
        call_count["value"] += 1
        if call_count["value"] == 1:
            return SimpleNamespace(
                output_text='{"shared_result":{"fit_score":0.72,"confidence_score":0.68,"risk_flags":[],"summary":"Promising profile"},"type_result":{"type_code":"INFJ"},"narrative":"Broken'
            )
        return SimpleNamespace(
            output_text="""{
  "shared_result": {
    "fit_score": 0.72,
    "confidence_score": 0.68,
    "risk_flags": [],
    "summary": "Promising profile"
  },
  "type_result": {
    "type_code": "INFJ",
    "dimension_scores": {
      "E_I": -0.4,
      "S_N": 0.5,
      "T_F": 0.2,
      "J_P": 0.1
    },
    "communication_style": "Thoughtful and empathetic",
    "work_style": "Reflective but structured",
    "growth_edges": ["State preferences earlier"]
  },
  "narrative": "The responses suggest a reflective and people-aware style with moderate confidence.",
  "prediction_text": "Likely INFJ-leaning, with some balanced preferences."
}"""
        )

    result = asyncio.run(
        assessment_scoring.evaluate_assessment(
            assessment_id=71,
            job_requirements_id=98,
            candidate_id=9,
            aggregated_data={
                "aggregated_traits": {
                    "reflection": {"mean_confidence": 0.7, "count": 2},
                    "empathy": {"mean_confidence": 0.66, "count": 2},
                },
                "top_traits": ["reflection", "empathy"],
                "behavioral_patterns": {"decision_making": "reflective"},
                "contradictions": [],
            },
            compare_traits_to_job_profile=None,
            generate_narrative=None,
            get_openai_client=lambda: object(),
            responses_create_async=_fake_responses_create_async,
        )
    )

    assert call_count["value"] == 2
    assert result.type_result["type_code"] == "INFJ"
    assert result.shared_result["fit_score"] == 0.72
    assert "low confidence" not in result.prediction_text.lower()
