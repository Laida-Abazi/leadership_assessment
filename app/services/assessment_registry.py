from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from typing import Callable

from fastapi import HTTPException

from app.as_requirements.config.models_setup import MODEL_FULL, get_openai_client
from app.db.models import AssessmentType, JobRequirements


@dataclass(frozen=True)
class AssessmentItemTemplate:
    key: str
    label: str
    prompt: str
    item_kind: str = "open_text"
    meta: dict | None = None


@dataclass(frozen=True)
class AssessmentTypeDefinition:
    code: str
    name: str
    version: str
    description: str
    capture_mode: str
    output_schema: dict
    agent_brief: str
    item_templates: tuple[AssessmentItemTemplate, ...]
    generation_mode: str = "static"


LEADERSHIP_ITEM_TEMPLATES = (
    AssessmentItemTemplate("behavioral_question", "Behavioral", ""),
    AssessmentItemTemplate("competency_based_question", "Competency Based", ""),
    AssessmentItemTemplate("situational_question", "Situational", ""),
    AssessmentItemTemplate("panel_question", "Panel", ""),
    AssessmentItemTemplate("business_case_question", "Business Case", ""),
    AssessmentItemTemplate("live_simulation_question", "Live Simulation", ""),
    AssessmentItemTemplate("psychometric_question", "Psychometric", ""),
    AssessmentItemTemplate("structured_reference_question", "Structured Reference", ""),
    AssessmentItemTemplate("culture_alignment_question", "Culture Alignment", ""),
    AssessmentItemTemplate("integrity_ethics_question", "Integrity And Ethics", ""),
)

MBTI_ITEM_TEMPLATES = (
    AssessmentItemTemplate(
        "energy_source",
        "Energy Source",
        "After an intense week, what usually helps you feel recharged again, and why does that work for you? If it depends on the situation, describe what changes for you.",
        meta={"dimension": "E/I"},
    ),
    AssessmentItemTemplate(
        "social_processing",
        "Social Processing",
        "When you're in a group discussion about something important, how do you usually process your thoughts before you speak? Walk me through what is happening internally for you in that moment.",
        meta={"dimension": "E/I"},
    ),
    AssessmentItemTemplate(
        "information_focus",
        "Information Focus",
        "When you're learning something new, what do you pay attention to first: concrete details, or the bigger pattern behind them? Tell me how that tends to shape the way you understand the whole situation.",
        meta={"dimension": "S/N"},
    ),
    AssessmentItemTemplate(
        "future_patterns",
        "Future Patterns",
        "When you think about the future, do you usually focus more on realistic next steps or on what could be possible beyond the obvious? Share how that tends to influence the choices you make.",
        meta={"dimension": "S/N"},
    ),
    AssessmentItemTemplate(
        "decision_basis",
        "Decision Basis",
        "When you have to make a difficult decision that affects other people, what matters most to you as you decide? If you can, describe the balance you try to strike.",
        meta={"dimension": "T/F"},
    ),
    AssessmentItemTemplate(
        "conflict_response",
        "Conflict Response",
        "When conflict shows up between people you work with, how do you usually respond, and what are you trying to protect or achieve? I’m especially interested in what guides your first instinct.",
        meta={"dimension": "T/F"},
    ),
    AssessmentItemTemplate(
        "structure_preference",
        "Structure Preference",
        "How do you usually feel about plans, schedules, and clear structure when you're trying to get something important done? Explain what helps you do your best work and what starts to feel restrictive.",
        meta={"dimension": "J/P"},
    ),
    AssessmentItemTemplate(
        "adaptability_style",
        "Adaptability Style",
        "If priorities suddenly change, how do you usually adapt in the moment, and what part of that feels easy or frustrating? If your answer depends on the context, talk me through that too.",
        meta={"dimension": "J/P"},
    ),
    AssessmentItemTemplate(
        "collaboration_preference",
        "Collaboration Preference",
        "When you're working on something meaningful with others, what kind of collaboration brings out your best thinking? Describe the kind of environment or interaction style that helps you contribute most naturally.",
        meta={"dimension": "E/I"},
    ),
    AssessmentItemTemplate(
        "planning_tension",
        "Planning Tension",
        "Which feels more natural to you: closing decisions and moving forward with a plan, or keeping options open until the picture becomes clearer? Share what usually makes you lean one way or the other.",
        meta={"dimension": "J/P"},
    ),
)

SLII_ITEM_TEMPLATES = (
    AssessmentItemTemplate(
        "new_hire_direction",
        "New Hire Direction",
        "",
    ),
    AssessmentItemTemplate(
        "wavering_commitment",
        "Wavering Commitment",
        "",
    ),
    AssessmentItemTemplate(
        "high_performer_autonomy",
        "High Performer Autonomy",
        "",
    ),
    AssessmentItemTemplate(
        "mixed_readiness_team",
        "Mixed Readiness Team",
        "",
    ),
    AssessmentItemTemplate(
        "style_mismatch_recovery",
        "Style Mismatch Recovery",
        "",
    ),
    AssessmentItemTemplate(
        "diagnose_before_acting",
        "Diagnose Before Acting",
        "",
    ),
    AssessmentItemTemplate(
        "urgency_under_pressure",
        "Urgency Under Pressure",
        "",
    ),
    AssessmentItemTemplate(
        "regression_response",
        "Regression Response",
        "",
    ),
    AssessmentItemTemplate(
        "delegation_boundaries",
        "Delegation Boundaries",
        "",
    ),
    AssessmentItemTemplate(
        "contracting_expectations",
        "Contracting Expectations",
        "",
    ),
)


ASSESSMENT_TYPE_DEFINITIONS: dict[str, AssessmentTypeDefinition] = {
    "leadership_core": AssessmentTypeDefinition(
        code="leadership_core",
        name="Leadership Core",
        version="v1",
        description="Role-specific leadership interview built from job requirements.",
        capture_mode="open_text",
        output_schema={
            "shared_result": ["fit_score", "confidence_score", "risk_flags", "trait_gaps"],
            "type_result": ["coverage_by_category", "top_traits"],
        },
        agent_brief=(
            "Treat this as a professional leadership interview focused on role evidence, decision-making, "
            "delivery, communication, and ethical judgment."
        ),
        item_templates=LEADERSHIP_ITEM_TEMPLATES,
        generation_mode="llm",
    ),
    "mbti": AssessmentTypeDefinition(
        code="mbti",
        name="MBTI",
        version="v1",
        description="Personality preference assessment for reflection across the four MBTI dichotomies.",
        capture_mode="open_text",
        output_schema={
            "shared_result": ["fit_score", "confidence_score", "risk_flags"],
            "type_result": ["type_code", "dimension_scores", "communication_style", "work_style", "growth_edges"],
        },
        agent_brief=(
            "Treat this as a reflective personality-preference conversation. Stay warm and exploratory, "
            "and do not frame the result as a diagnosis."
        ),
        item_templates=MBTI_ITEM_TEMPLATES,
        generation_mode="static",
    ),
    "slii": AssessmentTypeDefinition(
        code="slii",
        name="SLII",
        version="v1",
        description="Situational Leadership II assessment focused on diagnosing development levels and adapting style.",
        capture_mode="open_text",
        output_schema={
            "shared_result": ["fit_score", "confidence_score", "risk_flags"],
            "type_result": ["primary_style", "secondary_style", "adaptability_score", "mismatch_patterns", "coaching_suggestions"],
        },
        agent_brief=(
            "Treat this as a situational leadership assessment. Probe how the candidate diagnoses readiness "
            "and adapts style across directing, coaching, supporting, and delegating."
        ),
        item_templates=SLII_ITEM_TEMPLATES,
        generation_mode="llm",
    ),
}

ASSESSMENT_RESEARCH_MODEL = os.environ.get("OPENAI_ASSESSMENT_RESEARCH_MODEL", "o3-deep-research")


ASSESSMENT_GENERATION_PROMPT = """You are an expert in leadership and hiring assessments. Given the following job requirements, generate exactly 10 assessment questions (one for each required key).

Goal: produce role-specific, evidence-seeking interview questions that directly test this specific job's requirements, not generic interview prompts.

STRICT TAILORING RULES:
1) Requirement coverage:
   - Use ALL requirement categories present in the input: technical skills/frameworks/tools, responsibilities, domain/industry context, seniority, soft skills, culture fit, communication/language, constraints (location/availability/work authorization), education/certification, and relevant experience.
   - Distribute coverage across the 10 questions so no major requirement category is ignored.
2) Concrete specificity:
   - If a requirement names a specific framework/tool/platform/methodology (for example React, FastAPI, AWS, Docker, Scrum), mention that exact term in at least one question, preferably multiple when relevant.
   - If a responsibility is explicit (for example stakeholder management, architecture decisions, incident response), ask for concrete examples tied to that responsibility.
3) Evidence and depth:
   - Questions must force candidates to provide verifiable evidence: specific project context, decisions, trade-offs, metrics/results, and lessons learned.
   - Avoid broad prompts that can be answered vaguely.
   - Each question must have ONE primary objective only. Do not bundle several different competencies into one question.
   - Avoid long chained prompts like "how did you do X, Y, Z, and what was the result". Keep each question focused enough to be asked and answered in one turn.
4) Tone and format:
   - Natural, conversational, professional, and ready to read aloud.
   - No bullet points, no A/B lists, no "e.g.".
   - One question per value, each ending with a question mark.
   - Keep each question concise enough for voice delivery, ideally 1-2 sentences.

Return ONLY a valid JSON object with exactly these keys (use the key names as given); no other text.

Keys and meaning:
- behavioral_question: Past behavior in a real role-relevant situation, anchored to one or more stated requirements.
- competency_based_question: Demonstrated competency for this job, tied to required skills/responsibilities.
- situational_question: Hypothetical but realistic scenario from this role's environment and constraints.
- panel_question: A question suitable for multiple interviewers to assess cross-functional judgment and communication.
- business_case_question: Analysis and decision-making on a role-specific business/technical problem.
- live_simulation_question: Real-time role-play based on an authentic requirement from the job.
- psychometric_question: Work style or traits relevant to this role's pressure points and team context.
- structured_reference_question: Reference-check-oriented question targeting the most critical job requirements.
- culture_alignment_question: Values/team-fit question grounded in the stated culture and collaboration expectations.
- integrity_ethics_question: Ethical judgment question based on realistic dilemmas in this role/domain.

Job requirements to tailor questions to:
---
{job_summary}
---

Return only the JSON object with the 10 keys above, each value a single question string."""

MBTI_GENERATION_PROMPT = """You are designing a conversational MBTI-style reflection assessment.

Goal:
- Generate exactly 10 fresh questions for this interview.
- The questions must be similar in nature to common MBTI assessments, but not copied from any one source.
- The questions should help infer the four MBTI dichotomies:
  - Extraversion vs Introversion
  - Sensing vs Intuition
  - Thinking vs Feeling
  - Judging vs Perceiving

Important framing:
- This is a self-reflection tool, not a diagnosis.
- Questions should feel natural in a voice interview, not like a checkbox form.
- Avoid repeated wording and avoid obvious "Are you introverted or extroverted?" style questions.
- Ask for examples, tendencies, and trade-offs.
- Keep each question to one core idea.
- Keep each question concise enough to ask aloud in one turn.
- No more than 10 questions. Generate exactly 10.

Use these public-style example patterns only as inspiration, not as text to copy:
- Recharge preference after a long day: alone vs with people
- Group discussion behavior: reflect first vs speak early
- Problem solving: facts/details vs patterns/possibilities
- Feedback/conflict: logic/fairness vs harmony/impact
- Planning style: structure/closure vs flexibility/options
- Example either/or stems seen in mock MBTI questionnaires:
  - "I keep my thoughts to myself" vs "I speak up"
  - "I prefer to improvise" vs "I prefer to follow a clear procedure"
  - "I like to cooperate" vs "I like to compete"
  - "Makes lists" vs "Relies on memory"

Design guidance:
- Cover each dichotomy with at least 2 questions.
- Use a mix of work-context and everyday-behavior framing.
- Use open-ended wording so the same voice agent can ask them naturally.
- For each key below, produce one question string.

Optional role context to lightly adapt tone/examples:
---
{job_summary}
---

Return ONLY valid JSON with exactly these 10 keys:
- energy_source
- social_processing
- information_focus
- future_patterns
- decision_basis
- conflict_response
- structure_preference
- adaptability_style
- collaboration_preference
- planning_tension
"""

SLII_GENERATION_PROMPT = """You are designing a conversational Situational Leadership II style assessment.

Goal:
- Generate exactly 10 fresh scenario-based questions for this interview.
- The questions must be similar in nature to common SLII assessments, but not copied from any one source.
- The questions should reveal whether the leader can diagnose competence and commitment for a task and adapt among:
  - S1 Directing
  - S2 Coaching
  - S3 Supporting
  - S4 Delegating

Framework reminders:
- D1: low competence, high commitment
- D2: some competence, low or variable commitment
- D3: moderate/high competence, variable commitment/confidence
- D4: high competence, high commitment
- Same person can be at different development levels for different tasks.

Use these public-style scenario patterns only as inspiration, not as text to copy:
- A new employee is enthusiastic but inexperienced
- A team member has some skill but is discouraged or inconsistent
- A capable contributor lacks confidence
- A high performer wants autonomy
- A leader must choose how much direction, support, cadence, and decision-rights to provide
- Common sample situations mention declining performance, changing structures, urgent tasks, or mixed-readiness teams

Question design rules:
- Keep the questions scenario-based and open-ended.
- Ask what the leader would do and why.
- Force diagnosis of competence and commitment, not just generic leadership preference.
- Keep each question concise enough for voice delivery.
- No more than 10 questions. Generate exactly 10.
- Cover the full range of D1-D4 and S1-S4.
- Include at least one question about re-diagnosing when someone regresses or confidence changes.
- Include at least one question about contracting expectations/check-in cadence, not just style labels.

Optional role context to lightly adapt realism:
---
{job_summary}
---

Return ONLY valid JSON with exactly these 10 keys:
- new_hire_direction
- wavering_commitment
- high_performer_autonomy
- mixed_readiness_team
- style_mismatch_recovery
- diagnose_before_acting
- urgency_under_pressure
- regression_response
- delegation_boundaries
- contracting_expectations
"""


def get_assessment_definition(code: str | None) -> AssessmentTypeDefinition:
    normalized = (code or "leadership_core").strip().lower()
    if normalized not in ASSESSMENT_TYPE_DEFINITIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported assessment_type_code: {code}")
    return ASSESSMENT_TYPE_DEFINITIONS[normalized]


def list_assessment_definitions() -> list[AssessmentTypeDefinition]:
    return list(ASSESSMENT_TYPE_DEFINITIONS.values())


def job_requirements_to_summary(job: JobRequirements | None) -> str:
    if not job:
        return "No structured requirements provided."
    parts = []
    for attr, label in (
        ("requirement", "Summary/Requirement"),
        ("skill", "Skills"),
        ("soft_skill", "Soft skills"),
        ("experience", "Experience"),
        ("education", "Education"),
        ("certification", "Certification"),
        ("responsibility", "Responsibilities"),
        ("seniority_level", "Seniority"),
        ("culture_fit", "Culture fit"),
        ("industry_experience", "Industry experience"),
        ("role_experience", "Role experience"),
        ("language", "Language"),
        ("location", "Location"),
        ("availability", "Availability"),
        ("work_authorization", "Work authorization"),
    ):
        value = getattr(job, attr, None)
        if value:
            parts.append(f"{label}: {value}")
    return "\n".join(parts) if parts else "No structured requirements provided."


def ensure_assessment_types_seeded(db) -> None:
    existing = {
        row.code: row
        for row in db.query(AssessmentType).filter(AssessmentType.code.in_(ASSESSMENT_TYPE_DEFINITIONS.keys())).all()
    }
    mutated = False
    for definition in list_assessment_definitions():
        payload = {
            "name": definition.name,
            "version": definition.version,
            "description": definition.description,
            "config_json": {
                "capture_mode": definition.capture_mode,
                "output_schema": definition.output_schema,
                "agent_brief": definition.agent_brief,
                "items": [asdict(item) for item in definition.item_templates],
            },
            "active": True,
        }
        row = existing.get(definition.code)
        if row is None:
            db.add(AssessmentType(code=definition.code, **payload))
            mutated = True
            continue
        for key, value in payload.items():
            if getattr(row, key) != value:
                setattr(row, key, value)
                mutated = True
    if mutated:
        db.commit()


def build_assessment_items(
    definition: AssessmentTypeDefinition,
    *,
    job: JobRequirements | None = None,
    question_generator: Callable[[str], dict[str, str]] | None = None,
) -> list[dict]:
    if definition.generation_mode == "llm":
        generator = question_generator or _generate_questions_for_definition
        generated = generator(definition.code, job_requirements_to_summary(job))
        items: list[dict] = []
        for index, template in enumerate(definition.item_templates, start=1):
            items.append(
                {
                    "item_key": template.key,
                    "display_label": template.label,
                    "item_order": index,
                    "item_kind": template.item_kind,
                    "prompt_text": generated.get(template.key, ""),
                    "item_meta": template.meta or {},
                }
            )
        return items

    role_context = ""
    if job:
        summary = (job.requirement or job.role_experience or "").strip()
        if summary:
            role_context = f" Role context: {summary}"

    items = []
    for index, template in enumerate(definition.item_templates, start=1):
        prompt_text = template.prompt
        if role_context:
            prompt_text = f"{prompt_text}{role_context}"
        items.append(
            {
                "item_key": template.key,
                "display_label": template.label,
                "item_order": index,
                "item_kind": template.item_kind,
                "prompt_text": prompt_text,
                "item_meta": template.meta or {},
            }
        )
    return items


def _generate_questions_for_definition(assessment_code: str, job_summary: str) -> dict[str, str]:
    if assessment_code == "leadership_core":
        return _generate_leadership_questions(job_summary)
    if assessment_code == "mbti":
        return _generate_structured_questions(
            model=ASSESSMENT_RESEARCH_MODEL,
            prompt=MBTI_GENERATION_PROMPT.format(job_summary=job_summary),
            item_keys=[item.key for item in MBTI_ITEM_TEMPLATES],
        )
    if assessment_code == "slii":
        return _generate_structured_questions(
            model=ASSESSMENT_RESEARCH_MODEL,
            prompt=SLII_GENERATION_PROMPT.format(job_summary=job_summary),
            item_keys=[item.key for item in SLII_ITEM_TEMPLATES],
        )
    raise HTTPException(status_code=400, detail=f"Unsupported assessment type for question generation: {assessment_code}")


def _generate_leadership_questions(job_summary: str) -> dict[str, str]:
    return _generate_structured_questions(
        model=MODEL_FULL,
        prompt=ASSESSMENT_GENERATION_PROMPT.format(job_summary=job_summary),
        item_keys=[item.key for item in LEADERSHIP_ITEM_TEMPLATES],
    )


def _generate_structured_questions(*, model: str, prompt: str, item_keys: list[str]) -> dict[str, str]:
    client = get_openai_client()
    try:
        response = client.responses.create(
            model=model,
            input=prompt,
        )
        content = (response.output_text or "").strip()
    except Exception:
        # Keep the app usable even if the research model is unavailable in a given environment.
        fallback = client.chat.completions.create(
            model=MODEL_FULL,
            messages=[{"role": "user", "content": prompt}],
        )
        content = (fallback.choices[0].message.content or "").strip()
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
        content = content.strip()
    content = re.sub(r",\s*([}\]])", r"\1", content)
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"LLM returned invalid JSON: {exc}")
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="LLM did not return a JSON object")
    return {k: (str(data.get(k) or "")).strip() for k in item_keys}
