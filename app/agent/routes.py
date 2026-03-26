"""
Voice agent routes: Gemini Live (STS) WebSocket proxy for friendly conversation
and structured assessment interviews.
"""
import asyncio
import logging
import os
import re
import socket
import uuid
from pathlib import Path
from typing import Optional

import websockets.exceptions
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from app.db import SessionLocal
from app.db.models import Assessments, Responses, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])

# Gemini Live model for native audio (voice) conversation.
LIVE_MODEL = os.environ.get(
    "GEMINI_LIVE_MODEL",
    "gemini-2.5-flash-native-audio-preview-12-2025",
)

# Agent voice name.
AGENT_VOICE = os.environ.get("GEMINI_LIVE_VOICE", "Puck")

# Assessment question columns in order (must match Assessments model).
ASSESSMENT_QUESTION_FIELDS = [
    "behavioral_question",
    "competency_based_question",
    "situational_question",
    "panel_question",
    "business_case_question",
    "live_simulation_question",
    "psychometric_question",
    "structured_reference_question",
    "culture_alignment_question",
    "integrity_ethics_question",
]

DEFAULT_SYSTEM_INSTRUCTION = (
    "You are a friendly, warm voice assistant. Have a natural, conversational chat with the user. "
    "Keep responses concise and conversational so they work well in a voice dialogue. "
    "Be helpful and personable."
)

RESPONSE_FIELDS = [f.replace("_question", "_response") for f in ASSESSMENT_QUESTION_FIELDS]

# Minimum characters a transcript must have before we bother saving it.
MIN_TRANSCRIPT_LEN = 20

# How often (seconds) the autosave task flushes partial transcripts.
AUTOSAVE_INTERVAL_SEC = 5

# How many new characters must have arrived since the last autosave before we write.
AUTOSAVE_MIN_DELTA_CHARS = 30


# ── Helpers ──────────────────────────────────────────────────────────────────

def _preview_text(text: str, limit: int = 120) -> str:
    """Return a safe, single-line preview of text for logs."""
    if not text:
        return ""
    s = re.sub(r"\s+", " ", text).strip()
    if len(s) <= limit:
        return s
    return s[:limit].rstrip() + "…"

def _assessment_questions_list(assessment: Assessments) -> list[str]:
    """Return non-empty assessment questions in the canonical order."""
    out: list[str] = []
    for field in ASSESSMENT_QUESTION_FIELDS:
        val = getattr(assessment, field, None)
        if val and isinstance(val, str) and val.strip():
            out.append(val.strip())
    return out


def _response_fields_for_assessment(assessment: Assessments) -> list[str]:
    """Return response column names in the same order as the non-empty questions."""
    out: list[str] = []
    for field in ASSESSMENT_QUESTION_FIELDS:
        val = getattr(assessment, field, None)
        if val and isinstance(val, str) and val.strip():
            out.append(field.replace("_question", "_response"))
    return out


def _save_response(assessment_id: int, field_name: str, answer_text: str) -> bool:
    """Upsert the given response field for this assessment."""
    db = SessionLocal()
    try:
        row = db.query(Responses).filter(Responses.assessment_id == assessment_id).first()
        if row is None:
            row = Responses(assessment_id=assessment_id)
            db.add(row)
            db.flush()
        if hasattr(row, field_name):
            setattr(row, field_name, (answer_text or "").strip() or None)
        else:
            logger.warning(
                "Responses model has no field '%s' (assessment_id=%s) — row will be committed without updating that field.",
                field_name,
                assessment_id,
            )
        db.commit()
        logger.info(
            "Saved response — assessment_id=%s field=%s len=%s",
            assessment_id, field_name, len(answer_text),
        )
        logger.info(
            "Saved response preview — assessment_id=%s field=%s text='%s'",
            assessment_id,
            field_name,
            _preview_text(answer_text or ""),
        )
        return True
    except Exception as exc:
        logger.exception("Failed to save response: %s", exc)
        db.rollback()
        return False
    finally:
        db.close()


def _ensure_responses_row(assessment_id: int) -> None:
    """Ensure a Responses row exists for this assessment_id (creates it if missing)."""
    db = SessionLocal()
    try:
        row = db.query(Responses).filter(Responses.assessment_id == assessment_id).first()
        if row is None:
            db.add(Responses(assessment_id=assessment_id))
            db.commit()
            logger.info("Created Responses row for assessment_id=%s", assessment_id)
    except Exception as exc:
        logger.exception("Failed to ensure Responses row: %s", exc)
        db.rollback()
    finally:
        db.close()


def _count_saved_responses(assessment_id: int, response_fields: list[str]) -> int:
    """
    Return the number of *leading* consecutive fields that already have a value.
    Used on reconnect to skip questions already answered.
    """
    db = SessionLocal()
    try:
        row = db.query(Responses).filter(Responses.assessment_id == assessment_id).first()
        if not row:
            return 0
        count = 0
        for field in response_fields:
            val = getattr(row, field, None)
            if val and isinstance(val, str) and val.strip():
                count += 1
            else:
                break
        return count
    finally:
        db.close()


def _build_interview_system_instruction(
    questions: list[str],
    resumed: bool = False,
    candidate_name: str | None = None,
) -> str:
    """Build the system instruction for the assessment interview session."""
    if not questions:
        return DEFAULT_SYSTEM_INSTRUCTION

    clean_name = (candidate_name or "").strip()
    lines = [
        "You are conducting a structured leadership assessment interview as a professional, warm voice interviewer.",
        "",
        "Your role: ask the following questions ONE BY ONE, in order. You may rephrase slightly for natural speech, "
        "but the core meaning must remain intact. Wait for the candidate's FULL answer before moving on.",
        "",
        "Rules:",
    ]

    if resumed:
        lines.append(
            "- This session resumes an in-progress interview. Do NOT re-introduce yourself, say 'welcome back', "
            "or mention any break. Continue naturally with the next question below as though the conversation "
            "never stopped."
        )
    else:
        if clean_name:
            lines.append(
                f"- Open with a warm greeting, address the candidate by name: '{clean_name}'. "
                "Ask briefly how their day is going, wait for a short reply, then reassure them this will be "
                "a relaxed conversation. That exchange is warm-up ONLY — do NOT capture it as an answer. "
                "Begin the formal assessment with Question 1 in your very next speaking turn."
            )
        else:
            lines.append(
                "- Open with a warm greeting, ask briefly how their day is going, wait for a short reply, "
                "then reassure them. That exchange is warm-up ONLY. Begin the formal assessment with "
                "Question 1 in your very next speaking turn."
            )

    lines += [
        "- Do NOT label questions with numbers ('Question one', 'Question 1', etc.).",
        "- Use short, warm transitions between questions (e.g. 'Great, thanks for sharing that. Moving on…').",
        "- Ask each question clearly. A brief friendly lead-in is fine.",
        "- If you need elaboration on the SAME question, ask a natural follow-up — do NOT move to the next question until satisfied.",
        "- After an answer, acknowledge warmly and briefly, THEN ask the next question.",
        "- Do not skip questions or change their order.",
        "- Keep your own turns concise — this is a voice conversation.",
        "- After the final question is answered, thank the candidate warmly, tell them the interview is complete, "
        "and end on a positive note (e.g. 'You've given some fantastic insights — thank you so much!').",
        "- Do NOT say the interview is complete until ALL questions below have been asked and answered.",
        "",
        "Questions to ask (in this exact order):",
    ]
    for i, q in enumerate(questions, 1):
        lines.append(f"{i}. {q}")

    return "\n".join(lines)


def _normalize_question_text(text: str) -> str:
    """Normalize text for loose matching between output and canonical questions."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())).strip()


def _detect_question_from_output(output_text: str, questions: list[str], current_index: int) -> int | None:
    """Best-effort detection of which canonical question the agent just asked."""
    normalized_output = _normalize_question_text(output_text)
    if not normalized_output:
        return None

    search_order = list(range(max(current_index - 1, 0), min(current_index + 2, len(questions))))
    for idx in range(len(questions)):
        if idx not in search_order:
            search_order.append(idx)

    for idx in search_order:
        normalized_question = _normalize_question_text(questions[idx])
        if not normalized_question:
            continue
        if normalized_question in normalized_output:
            return idx
    return None


# ── WebSocket handler ─────────────────────────────────────────────────────────

@router.get("/test", response_class=FileResponse)
def serve_test_agent_page():
    """Serve the test agent frontend (friendly voice conversation)."""
    path = Path(__file__).resolve().parent.parent / "templates" / "test_agent.html"
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")
    return FileResponse(path)


@router.websocket("/ws")
async def agent_websocket(websocket: WebSocket):
    """
    WebSocket proxy to Gemini Live API.

    Query params
    ------------
    assessment_id : int (optional)
        When set the agent conducts the structured assessment interview and
        saves each answer to the Responses table as it is spoken.

    Audio contract
    --------------
    Client → server : raw 16-bit PCM, 16 kHz, mono (no header)
    Server → client : raw 16-bit PCM, 24 kHz, mono (no header)
    """
    session_id = uuid.uuid4().hex[:10]
    client_host = None
    try:
        client_host = getattr(getattr(websocket, "client", None), "host", None)
    except Exception:
        client_host = None

    logger.info(
        "[%s] WebSocket connect: client=%s query=%s",
        session_id,
        client_host,
        dict(getattr(websocket, "query_params", {}) or {}),
    )

    await websocket.accept()
    logger.info("[%s] WebSocket accepted.", session_id)

    # ── Resolve assessment (if any) ───────────────────────────────────────────
    assessment_id: Optional[int] = None
    raw_id = websocket.query_params.get("assessment_id")
    if raw_id:
        try:
            assessment_id = int(raw_id)
        except ValueError:
            pass
    logger.info("[%s] assessment_id parsed: raw=%r parsed=%s", session_id, raw_id, assessment_id)

    system_instruction = DEFAULT_SYSTEM_INSTRUCTION
    response_fields: list[str] = []   # column names, parallel to `questions`
    questions: list[str] = []
    candidate_name: str | None = None
    already_saved = 0                 # questions answered in a previous session

    if assessment_id is not None:
        db = SessionLocal()
        try:
            assessment = db.get(Assessments, assessment_id)
            if assessment:
                logger.info("[%s] Assessment found: id=%s user_id=%s", session_id, assessment_id, assessment.user_id)
                user = db.get(User, assessment.user_id)
                if user:
                    candidate_name = (
                        f"{(user.name or '').strip()} {(user.surname or '').strip()}".strip() or None
                    )
                    logger.info("[%s] Candidate resolved: %r", session_id, candidate_name)
                else:
                    logger.warning(
                        "[%s] Candidate user missing: assessment_id=%s user_id=%s",
                        session_id,
                        assessment_id,
                        assessment.user_id,
                    )
                questions = _assessment_questions_list(assessment)
                response_fields = _response_fields_for_assessment(assessment)
                logger.info(
                    "[%s] Interview mapping: total_questions=%s response_fields=%s",
                    session_id,
                    len(questions),
                    response_fields,
                )
                already_saved = _count_saved_responses(assessment_id, response_fields)
                if response_fields:
                    # Create the row early so the assessment_id exists in Responses even before a "long enough" answer.
                    _ensure_responses_row(assessment_id)
                logger.info(
                    "[%s] Assessment ready: id=%s total_questions=%s already_saved=%s",
                    session_id,
                    assessment_id,
                    len(questions),
                    already_saved,
                )
            else:
                logger.warning("[%s] Assessment id=%s not found — using default instruction.", session_id, assessment_id)
        finally:
            db.close()

    # ── Shared events / state ─────────────────────────────────────────────────
    client_disconnected = asyncio.Event()
    interview_complete = asyncio.Event()

    # Raw PCM chunks from the client; None is the "stop" sentinel.
    audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=300)

    # ─────────────────────────────────────────────────────────────────────────
    # Answer-tracking state  (session-based model)
    # ─────────────────────────────────────────────────────────────────────────
    # A question is treated as an open SESSION, not a sequence of turns.
    #
    #   question_session_open = True  → all user speech is appended to
    #                                   state["transcript"] for the current q.
    #   input_transcription.finished  → partial snapshot only; does NOT advance.
    #   Agent output classified as NEW_QUESTION / COMPLETE → session boundary:
    #                                   persist full accumulated transcript,
    #                                   advance current_q, open new session.
    #
    # Key fields
    # ----------
    #   current_q               – index into questions / response_fields
    #   transcript              – ACCUMULATED text for the CURRENT question
    #   last_persisted          – last text written to DB (delta guard)
    #   in_warmup               – True until warm-up exchange is complete
    #   agent_spoke             – has Gemini produced any audio/text yet?
    #   awaiting_user_turn_end  – user speech received, not yet closed
    #   current_question_anchor_seen – agent confirmed to have asked this q
    #   output_transcript       – accumulates agent speech for classification
    #   question_session_open   – True = inside an active question session
    #   agent_turn_classification – last classifier result for agent output
    # ─────────────────────────────────────────────────────────────────────────

    state: dict = {
        "current_q": already_saved,
        "transcript": "",
        "last_persisted": "",
        "agent_spoke": False,
        "awaiting_user_turn_end": False,
        "in_warmup": (assessment_id is not None and bool(response_fields) and already_saved == 0),
        "first_question_capture_logged": False,
        "current_question_anchor_seen": already_saved > 0,
        "output_transcript": "",
        # Session-based state — True immediately on resume; set True when warm-up ends.
        "question_session_open": (already_saved > 0),
        "agent_turn_classification": "CONTINUE",
    }
    logger.info(
        "[%s] State init: interview_mode=%s in_warmup=%s current_q=%s/%s session_open=%s",
        session_id,
        (assessment_id is not None and bool(response_fields)),
        state["in_warmup"],
        state["current_q"],
        len(response_fields),
        state["question_session_open"],
    )
    if assessment_id is not None and response_fields:
        if already_saved == 0 and questions:
            logger.info(
                "[%s] Warm-up active. Formal DB capture starts after first user turn finishes. "
                "Next capture target: q=1/%s field=%s question='%s'",
                session_id,
                len(response_fields),
                response_fields[0],
                _preview_text(questions[0], limit=220),
            )
        elif already_saved < len(response_fields):
            logger.info(
                "[%s] Resume mode (warm-up skipped). Next capture target: q=%s/%s field=%s question='%s'",
                session_id,
                already_saved + 1,
                len(response_fields),
                response_fields[already_saved],
                _preview_text(questions[already_saved], limit=220),
            )
            logger.info(
                "[%s] Awaiting question anchor for resumed q=%s before advancing.",
                session_id,
                already_saved + 1,
            )

    def _is_interview_mode() -> bool:
        return assessment_id is not None and bool(response_fields)

    def _classify_agent_turn(text: str) -> str:
        """
        Classify what the agent just said to determine session-flow control.

        Returns one of:
          "COMPLETE"     – agent has closed the interview
          "NEW_QUESTION" – agent has transitioned to the next question
          "CONTINUE"     – follow-up / clarification on the same question
        """
        lower = (text or "").lower()

        complete_signals = [
            "interview is complete",
            "that concludes",
            "that's all",
            "we're done",
            "fantastic insights",
            "thank you so much",
            "thank you for your time",
            "thank you for participating",
        ]
        for sig in complete_signals:
            if sig in lower:
                return "COMPLETE"

        # Hard signal: the output already contains canonical text from the NEXT question.
        detected_q = _detect_question_from_output(text, questions, state["current_q"])
        if detected_q is not None and detected_q > state["current_q"]:
            return "NEW_QUESTION"

        new_question_signals = [
            "moving on",
            "next question",
            "let's talk about",
            "let's move on",
            "great, thanks for sharing",
            "now i'd like to ask",
            "now, i'd like to ask",
            "i'd like to move",
            "i'd like to now ask",
        ]
        for sig in new_question_signals:
            if sig in lower:
                return "NEW_QUESTION"

        return "CONTINUE"

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _persist_current(reason: str, min_len: int = MIN_TRANSCRIPT_LEN) -> bool:
        """Write `state['transcript']` for `state['current_q']` to the DB."""
        if not _is_interview_mode():
            logger.debug("[%s] Persist skipped (not interview mode). reason=%s", session_id, reason)
            return False
        text = (state["transcript"] or "").strip()
        if len(text) < min_len:
            logger.debug(
                "[%s] Persist skipped (len=%s < min_len=%s). reason=%s q=%s/%s text='%s'",
                session_id,
                len(text),
                min_len,
                reason,
                state["current_q"] + 1,
                len(response_fields),
                _preview_text(text),
            )
            return False
        idx = state["current_q"]
        if idx >= len(response_fields):
            logger.debug(
                "[%s] Persist skipped (idx out of range). reason=%s idx=%s total=%s",
                session_id,
                reason,
                idx,
                len(response_fields),
            )
            return False
        logger.info(
            "[%s] Persist attempt: reason=%s assessment_id=%s q=%s/%s field=%s len=%s",
            session_id,
            reason,
            assessment_id,
            idx + 1,
            len(response_fields),
            response_fields[idx],
            len(text),
        )
        ok = _save_response(assessment_id, response_fields[idx], text)  # type: ignore[arg-type]
        if ok:
            state["last_persisted"] = text
            logger.info("[%s] Persist OK: q=%s field=%s", session_id, idx + 1, response_fields[idx])
        else:
            logger.error("[%s] Persist FAILED: q=%s field=%s", session_id, idx + 1, response_fields[idx])
        return ok

    def _advance_question() -> None:
        """Move to the next question, resetting per-question state."""
        prev_idx = state["current_q"]
        state["current_q"] += 1
        state["transcript"] = ""
        state["last_persisted"] = ""
        state["awaiting_user_turn_end"] = False
        state["current_question_anchor_seen"] = False
        logger.info(
            "[%s] Advanced question: %s -> %s (next=%s/%s) assessment_id=%s",
            session_id,
            prev_idx,
            state["current_q"],
            state["current_q"] + 1,
            len(response_fields),
            assessment_id,
        )

    def _on_user_turn_finished() -> None:
        """
        Called when input_transcription.finished = True.

        This no longer advances current_q.  It only saves a partial snapshot of
        the current accumulating transcript so data is not lost on disconnect.
        Warm-up handling is unchanged: the first finished event after the agent
        has spoken discards the warm-up reply and opens the first question session.
        """
        if not _is_interview_mode():
            return

        if state["in_warmup"]:
            logger.info(
                "[%s] Warm-up finished — discarding. assessment_id=%s transcript_len=%s preview='%s'",
                session_id,
                assessment_id,
                len((state["transcript"] or "").strip()),
                _preview_text((state["transcript"] or "").strip()),
            )
            state["in_warmup"] = False
            state["transcript"] = ""
            state["last_persisted"] = ""
            state["awaiting_user_turn_end"] = False
            state["question_session_open"] = True
            if response_fields:
                logger.info(
                    "[%s] Warm-up complete. Starting formal DB capture at q=1/%s field=%s question='%s'",
                    session_id,
                    len(response_fields),
                    response_fields[0],
                    _preview_text(questions[0] if questions else "", limit=220),
                )
            return

        # Partial save only — do NOT advance current_q.
        text = (state["transcript"] or "").strip()
        if text:
            logger.info(
                "[%s] Sub-turn snapshot: assessment_id=%s q=%s/%s len=%s preview='%s'",
                session_id,
                assessment_id,
                state["current_q"] + 1,
                len(response_fields),
                len(text),
                _preview_text(text),
            )
            _persist_current("user_sub_turn_snapshot", min_len=0)
        state["awaiting_user_turn_end"] = False

    def _on_agent_classified_new_question() -> None:
        """
        Called when _classify_agent_turn() returns NEW_QUESTION.

        This is the ONLY place current_q advances (besides interview completion
        via COMPLETE).  Persists the full accumulated transcript for the current
        question, then advances the index and opens a new session.
        """
        if not _is_interview_mode() or state["in_warmup"]:
            return

        text = (state["transcript"] or "").strip()
        if text:
            logger.info(
                "[%s] Agent NEW_QUESTION signal — persisting q=%s/%s len=%s preview='%s'",
                session_id,
                state["current_q"] + 1,
                len(response_fields),
                len(text),
                _preview_text(text),
            )
            _persist_current("agent_new_question_signal", min_len=0)
        else:
            logger.info(
                "[%s] Agent NEW_QUESTION signal — empty transcript, skipping persist. q=%s/%s",
                session_id,
                state["current_q"] + 1,
                len(response_fields),
            )

        _advance_question()
        state["question_session_open"] = True
        state["output_transcript"] = ""   # reset accumulator for the new question

        if state["current_q"] >= len(response_fields) and not interview_complete.is_set():
            interview_complete.set()

    def signal_client_disconnect() -> None:
        if client_disconnected.is_set():
            return
        client_disconnected.set()
        # Wake any coroutine blocked on audio_queue.get().
        try:
            audio_queue.put_nowait(None)
        except asyncio.QueueFull:
            try:
                audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                audio_queue.put_nowait(None)
            except asyncio.QueueFull:
                logger.warning("Could not enqueue disconnect sentinel; audio queue full.")

    # ── Config builder ────────────────────────────────────────────────────────

    def _build_session_config() -> dict:
        """Build the Gemini session config, reflecting current progress."""
        from google.genai import types  # local import — already imported in handler

        cfg: dict = {
            "response_modalities": ["AUDIO"],
            "speech_config": {
                "voice_config": {
                    "prebuilt_voice_config": {"voice_name": AGENT_VOICE},
                },
            },
        }

        if _is_interview_mode():
            saved = state["current_q"]
            remaining = questions[saved:]
            resumed = saved > 0
            cfg["system_instruction"] = (
                _build_interview_system_instruction(remaining, resumed=resumed, candidate_name=candidate_name)
                if remaining
                else DEFAULT_SYSTEM_INSTRUCTION
            )
            cfg["input_audio_transcription"] = types.AudioTranscriptionConfig()
            cfg["output_audio_transcription"] = types.AudioTranscriptionConfig()
        else:
            cfg["system_instruction"] = system_instruction

        return cfg

    # ── Top-level tasks ───────────────────────────────────────────────────────

    async def read_client_audio() -> None:
        """Pump raw PCM bytes from the client WebSocket into audio_queue."""
        consecutive_errors = 0
        total_audio_bytes = 0
        total_audio_frames = 0
        try:
            while True:
                try:
                    msg = await websocket.receive()
                except WebSocketDisconnect as exc:
                    code = getattr(exc, "code", 1000)
                    logger.info("[%s] Client WebSocket closed (code=%s).", session_id, code)
                    break
                except Exception as exc:
                    if "disconnect message has been received" in str(exc).lower():
                        logger.info("[%s] Client receive loop reached terminal disconnect state: %s", session_id, exc)
                        break
                    consecutive_errors += 1
                    logger.warning("[%s] Transient receive error #%s: %s", session_id, consecutive_errors, exc)
                    if consecutive_errors >= 5:
                        logger.error("[%s] Too many consecutive receive errors — treating as disconnect.", session_id)
                        break
                    await asyncio.sleep(0.1)
                    continue

                consecutive_errors = 0
                msg_type = msg.get("type")
                if msg_type == "websocket.disconnect":
                    logger.info(
                        "[%s] Client disconnect frame (code=%s). total_audio_frames=%s total_audio_bytes=%s",
                        session_id,
                        msg.get("code", 1000),
                        total_audio_frames,
                        total_audio_bytes,
                    )
                    break
                if msg_type != "websocket.receive":
                    logger.debug("[%s] Ignoring ws message type=%s", session_id, msg_type)
                    continue

                data: bytes | None = msg.get("bytes")
                if data:
                    total_audio_frames += 1
                    total_audio_bytes += len(data)
                    if total_audio_frames <= 3:
                        logger.info(
                            "[%s] First audio frame received: bytes=%s",
                            session_id,
                            len(data),
                        )
                else:
                    text = msg.get("text")
                    if text is not None:
                        logger.debug(
                            "[%s] Received text frame from client (len=%s): %r",
                            session_id,
                            len(text),
                            _preview_text(text, limit=200),
                        )
                    else:
                        logger.debug("[%s] Received empty ws.receive frame (no bytes/text).", session_id)
                    continue
                if not data:
                    continue

                try:
                    audio_queue.put_nowait(data)
                except asyncio.QueueFull:
                    try:
                        audio_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    audio_queue.put_nowait(data)

        except Exception as exc:
            logger.exception("[%s] Unexpected error reading client audio: %s", session_id, exc)
        finally:
            # Best-effort: save any partial transcript the user may have spoken.
            if _is_interview_mode() and not state["in_warmup"]:
                logger.info(
                    "[%s] Client disconnect flush: assessment_id=%s q=%s/%s len=%s",
                    session_id,
                    assessment_id,
                    state["current_q"] + 1,
                    len(response_fields),
                    len((state["transcript"] or "").strip()),
                )
                _persist_current("client_disconnect_flush")
            signal_client_disconnect()
            logger.info(
                "[%s] read_client_audio finished. total_audio_frames=%s total_audio_bytes=%s",
                session_id,
                total_audio_frames,
                total_audio_bytes,
            )

    async def autosave_answers() -> None:
        """Periodically persist partial transcripts during long user turns."""
        while not client_disconnected.is_set() and not interview_complete.is_set():
            await asyncio.sleep(AUTOSAVE_INTERVAL_SEC)
            if client_disconnected.is_set() or interview_complete.is_set():
                break
            if not _is_interview_mode() or state["in_warmup"]:
                continue
            text = (state["transcript"] or "").strip()
            last = state["last_persisted"]
            if len(text) < MIN_TRANSCRIPT_LEN:
                logger.debug(
                    "[%s] Autosave skip (too short): assessment_id=%s q=%s/%s len=%s min=%s",
                    session_id,
                    assessment_id,
                    state["current_q"] + 1,
                    len(response_fields),
                    len(text),
                    MIN_TRANSCRIPT_LEN,
                )
                continue
            if len(text) - len(last) < AUTOSAVE_MIN_DELTA_CHARS:
                logger.debug(
                    "[%s] Autosave skip (delta too small): assessment_id=%s q=%s/%s len=%s last=%s delta=%s min_delta=%s",
                    session_id,
                    assessment_id,
                    state["current_q"] + 1,
                    len(response_fields),
                    len(text),
                    len(last or ""),
                    len(text) - len(last or ""),
                    AUTOSAVE_MIN_DELTA_CHARS,
                )
                continue
            logger.info(
                "[%s] Autosave persist: assessment_id=%s q=%s/%s len=%s preview='%s'",
                session_id,
                assessment_id,
                state["current_q"] + 1,
                len(response_fields),
                len(text),
                _preview_text(text),
            )
            _persist_current("autosave")

    async def keepalive_client() -> None:
        """Send periodic JSON pings to keep the client WebSocket alive through proxies."""
        while not client_disconnected.is_set():
            await asyncio.sleep(20)
            if client_disconnected.is_set():
                break
            try:
                await websocket.send_json({"ping": True})
            except (WebSocketDisconnect, Exception) as exc:
                logger.info("Keepalive failed — client disconnected: %s", exc)
                signal_client_disconnect()
                break

    async def gemini_session_loop() -> None:
        """
        Manages one or more Gemini Live sessions for the lifetime of the client
        connection.  Reconnects transparently when Gemini closes the session
        (e.g. the 10-minute hard timeout) as long as the client is still connected
        and the interview is not yet complete.
        """
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            logger.exception("google-genai not installed")
            await websocket.send_json({"error": "Server missing google-genai. Install with: pip install google-genai"})
            await websocket.close(1000, "Server missing google-genai.")
            return

        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            await websocket.send_json({"error": "GOOGLE_API_KEY or GEMINI_API_KEY not set"})
            await websocket.close(1000, "GOOGLE_API_KEY or GEMINI_API_KEY not set")
            return

        client = genai.Client(api_key=api_key)
        MAX_RECONNECTS = 10
        RECONNECT_DELAY = 2.0
        reconnect_count = 0

        while not client_disconnected.is_set() and not interview_complete.is_set():
            if reconnect_count > 0:
                logger.info(
                    "Reconnecting to Gemini (attempt %s/%s) — question index %s",
                    reconnect_count, MAX_RECONNECTS, state["current_q"],
                )
                await asyncio.sleep(RECONNECT_DELAY)
                try:
                    await websocket.send_json({"status": "reconnecting", "attempt": reconnect_count})
                except Exception:
                    pass

            if reconnect_count >= MAX_RECONNECTS:
                logger.error("Exceeded max Gemini reconnect attempts (%s).", MAX_RECONNECTS)
                try:
                    await websocket.send_json({"error": "Could not maintain Gemini connection after multiple attempts."})
                except Exception:
                    pass
                break

            config = _build_session_config()

            try:
                async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:
                    # Send a silent priming frame so Gemini doesn't time out before
                    # the user starts speaking.  3200 bytes = 100 ms at 16 kHz 16-bit mono.
                    await session.send_realtime_input(
                        audio=types.Blob(data=b"\x00" * 3200, mime_type="audio/pcm;rate=16000")
                    )

                    status = "reconnected" if reconnect_count > 0 else "connected"
                    try:
                        await websocket.send_json({"status": status, "model": LIVE_MODEL})
                    except Exception:
                        signal_client_disconnect()
                        return

                    # ── Sub-task: audio_queue → Gemini ────────────────────────
                    async def pump_audio_to_gemini() -> None:
                        KEEP_ALIVE_INTERVAL = 0.5   # seconds; silence if no real audio arrives
                        SILENCE = b"\x00" * 1600    # 50 ms at 16 kHz 16-bit mono
                        sent_frames = 0
                        sent_bytes = 0
                        keepalive_frames = 0
                        first_real_audio_logged = False

                        try:
                            while not client_disconnected.is_set():
                                try:
                                    chunk = await asyncio.wait_for(
                                        audio_queue.get(), timeout=KEEP_ALIVE_INTERVAL
                                    )
                                except asyncio.TimeoutError:
                                    # Send silence keep-alive so Gemini's VAD stays active.
                                    try:
                                        await session.send_realtime_input(
                                            audio=types.Blob(data=SILENCE, mime_type="audio/pcm;rate=16000")
                                        )
                                        keepalive_frames += 1
                                    except Exception as exc:
                                        logger.debug(
                                            "[%s] Silence keep-alive failed (session closed): %s",
                                            session_id,
                                            exc,
                                        )
                                        return
                                    continue

                                if chunk is None:
                                    # Disconnect sentinel — put it back for any future iteration.
                                    await audio_queue.put(None)
                                    return

                                try:
                                    await session.send_realtime_input(
                                        audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                                    )
                                    sent_frames += 1
                                    sent_bytes += len(chunk)
                                    if not first_real_audio_logged:
                                        first_real_audio_logged = True
                                        logger.info(
                                            "[%s] First audio chunk forwarded to Gemini: bytes=%s",
                                            session_id,
                                            len(chunk),
                                        )
                                except Exception as exc:
                                    logger.debug(
                                        "[%s] Audio send failed (session likely closed): %s",
                                        session_id,
                                        exc,
                                    )
                                    # Return the chunk so it isn't lost on reconnect.
                                    try:
                                        audio_queue.put_nowait(chunk)
                                    except asyncio.QueueFull:
                                        pass
                                    return
                        finally:
                            logger.info(
                                "[%s] pump_audio_to_gemini finished: sent_frames=%s sent_bytes=%s keepalive_frames=%s",
                                session_id,
                                sent_frames,
                                sent_bytes,
                                keepalive_frames,
                            )

                    # ── Sub-task: Gemini → client ────────────────────────────
                    async def receive_from_gemini() -> None:
                        logged_keys = False
                        rotation_requested = False
                        answers_saved_notified = False

                        try:
                            while not client_disconnected.is_set():
                                turn_had_messages = False

                                async for message in session.receive():
                                    turn_had_messages = True

                                    try:
                                        # ── Debug: log message structure once ─────────────
                                        if not logged_keys:
                                            try:
                                                dump = getattr(message, "model_dump", lambda **k: {})(exclude_none=True)
                                                logger.info("Gemini message keys (first): %s", list(dump.keys()))
                                            except Exception:
                                                logger.info("Gemini message type: %s", type(message).__name__)
                                            logged_keys = True

                                        # ── go_away → rotate session after turn ───────────
                                        go_away = getattr(message, "go_away", None)
                                        if go_away is not None:
                                            logger.warning("Gemini go_away: %s", go_away)
                                            rotation_requested = True

                                        sc = getattr(message, "server_content", None)
                                        if sc is None:
                                            continue

                                        turn_complete: bool = bool(getattr(sc, "turn_complete", False))
                                        model_turn = getattr(sc, "model_turn", None)
                                        if turn_complete:
                                            logger.debug(
                                                "[%s] Gemini turn_complete=True: assessment_id=%s q=%s/%s warmup=%s awaiting_end=%s transcript_len=%s",
                                                session_id,
                                                assessment_id,
                                                state["current_q"] + 1,
                                                len(response_fields),
                                                state["in_warmup"],
                                                bool(state.get("awaiting_user_turn_end")),
                                                len((state.get("transcript") or "").strip()),
                                            )

                                        # ── Output transcription (agent speech) ───────────
                                        out_trans = getattr(sc, "output_transcription", None)
                                        if out_trans is not None:
                                            out_text = (getattr(out_trans, "text", None) or "").strip()
                                            if out_text and _is_interview_mode():
                                                state["agent_spoke"] = True
                                                state["output_transcript"] = (
                                                    f"{state.get('output_transcript', '')} {out_text}"
                                                ).strip()

                                                # Classify agent output to drive session flow.
                                                classification = _classify_agent_turn(
                                                    state["output_transcript"]
                                                )
                                                state["agent_turn_classification"] = classification

                                                if classification == "NEW_QUESTION" and not state["in_warmup"]:
                                                    _on_agent_classified_new_question()

                                                elif classification == "COMPLETE" and not state["in_warmup"]:
                                                    logger.info(
                                                        "[%s] Agent COMPLETE signal detected. assessment_id=%s",
                                                        session_id,
                                                        assessment_id,
                                                    )
                                                    text = (state.get("transcript") or "").strip()
                                                    if text:
                                                        _persist_current("agent_complete_signal", min_len=0)
                                                    if not interview_complete.is_set():
                                                        interview_complete.set()

                                                # Anchor detection — used only for warm-up exit
                                                # and same-question confirmation.
                                                detected_q = _detect_question_from_output(
                                                    state["output_transcript"],
                                                    questions,
                                                    state["current_q"],
                                                )
                                                if detected_q is not None:
                                                    if state["in_warmup"] and detected_q == 0:
                                                        state["in_warmup"] = False
                                                        state["transcript"] = ""
                                                        state["last_persisted"] = ""
                                                        state["awaiting_user_turn_end"] = False
                                                        state["question_session_open"] = True
                                                        logger.info(
                                                            "[%s] Warm-up complete via anchor detection. "
                                                            "Session open. q=1/%s field=%s",
                                                            session_id,
                                                            len(response_fields),
                                                            response_fields[0] if response_fields else "n/a",
                                                        )
                                                    elif detected_q == state["current_q"]:
                                                        state["current_question_anchor_seen"] = True
                                                        logger.debug(
                                                            "[%s] Question anchor confirmed: q=%s/%s",
                                                            session_id,
                                                            detected_q + 1,
                                                            len(response_fields),
                                                        )

                                        # ── Input transcription (user speech) ────────────
                                        in_trans = getattr(sc, "input_transcription", None)
                                        if in_trans is not None and _is_interview_mode():
                                            chunk_text: str = getattr(in_trans, "text", None) or ""
                                            finished: bool = bool(getattr(in_trans, "finished", False))

                                            # Accumulate ONLY inside an open question session.
                                            # No space injected between chunks — Gemini sends sub-word fragments
                                            # that already carry their own whitespace; adding an extra " " produces
                                            # garbled output like "I  on ce  intro du ced".
                                            if chunk_text and state["question_session_open"] and not state["in_warmup"]:
                                                state["transcript"] = (
                                                    (state["transcript"] or "") + chunk_text
                                                ).strip()
                                                state["awaiting_user_turn_end"] = True
                                                if (
                                                    not state.get("first_question_capture_logged")
                                                    and state["current_q"] < len(response_fields)
                                                ):
                                                    logger.info(
                                                        "[%s] Formal answer capture started: q=%s/%s field=%s question='%s'",
                                                        session_id,
                                                        state["current_q"] + 1,
                                                        len(response_fields),
                                                        response_fields[state["current_q"]],
                                                        _preview_text(questions[state["current_q"]], limit=220)
                                                        if state["current_q"] < len(questions) else "",
                                                    )
                                                    state["first_question_capture_logged"] = True

                                            if finished:
                                                if state["in_warmup"] and not state["agent_spoke"]:
                                                    # Agent hasn't greeted yet — too early to exit warm-up.
                                                    logger.info(
                                                        "[%s] User spoke before agent greeted — discarding (pre-warmup). "
                                                        "assessment_id=%s",
                                                        session_id,
                                                        assessment_id,
                                                    )
                                                    state["transcript"] = ""
                                                else:
                                                    # finished=True means user stopped speaking for this sub-turn.
                                                    # It is NOT a question boundary — only save a partial snapshot.
                                                    logger.info(
                                                        "[%s] input_transcription.finished=True — partial snapshot. "
                                                        "q=%s/%s warmup=%s session_open=%s transcript_len=%s preview='%s'",
                                                        session_id,
                                                        state["current_q"] + 1,
                                                        len(response_fields),
                                                        state["in_warmup"],
                                                        state["question_session_open"],
                                                        len((state.get("transcript") or "").strip()),
                                                        _preview_text((state.get("transcript") or "").strip(), limit=260),
                                                    )
                                                    _on_user_turn_finished()

                                                # Progress notification to client.
                                                if not state["in_warmup"] and _is_interview_mode():
                                                    try:
                                                        await websocket.send_json({
                                                            "question_index": state["current_q"],
                                                            "total_questions": len(response_fields),
                                                        })
                                                    except Exception:
                                                        pass

                                        if turn_complete and _is_interview_mode():
                                            # Reset agent-turn output accumulator (classifier starts fresh).
                                            state["output_transcript"] = ""

                                        # ── Forward audio to client ───────────────────────
                                        parts = None
                                        if model_turn and getattr(model_turn, "parts", None):
                                            parts = model_turn.parts
                                        if not parts:
                                            parts = getattr(sc, "parts", None)

                                        if parts:
                                            for part in parts:
                                                inline = getattr(part, "inline_data", None)
                                                if inline and getattr(inline, "data", None):
                                                    try:
                                                        await websocket.send_bytes(inline.data)
                                                        # Mark that the agent has produced audio.
                                                        if _is_interview_mode():
                                                            state["agent_spoke"] = True
                                                    except Exception as exc:
                                                        logger.warning("Failed to send audio to client: %s", exc)
                                                        signal_client_disconnect()
                                                        return

                                        # ── Interruption signal ───────────────────────────
                                        if getattr(sc, "interrupted", False):
                                            try:
                                                await websocket.send_json({"interrupted": True})
                                            except Exception:
                                                signal_client_disconnect()
                                                return

                                        # ── Notify client when all answers saved ──────────
                                        # interview_complete can be set by _on_agent_classified_new_question()
                                        # or the COMPLETE handler; send the JSON notification here (async ctx).
                                        if (
                                            _is_interview_mode()
                                            and interview_complete.is_set()
                                            and not answers_saved_notified
                                        ):
                                            answers_saved_notified = True
                                            logger.info(
                                                "[%s] All %s responses saved — assessment_id=%s.",
                                                session_id,
                                                len(response_fields),
                                                assessment_id,
                                            )
                                            try:
                                                await websocket.send_json({"answers_saved": True})
                                            except Exception:
                                                pass

                                        # ── Session rotation after go_away ────────────────
                                        if rotation_requested and turn_complete:
                                            logger.info(
                                                "Rotating Gemini session at question index %s.",
                                                state["current_q"],
                                            )
                                            return

                                    except Exception as exc:
                                        logger.warning("Error handling Gemini message: %s", exc)

                                if not turn_had_messages:
                                    # session.receive() returned with zero messages →
                                    # the Gemini WebSocket has closed server-side.
                                    logger.info(
                                        "Gemini session closed server-side.  answered=%s/%s",
                                        state["current_q"], len(response_fields),
                                    )
                                    break
                                # Normal turn end — loop to await the next turn.

                        except asyncio.CancelledError:
                            pass
                        except websockets.exceptions.ConnectionClosedError as exc:
                            if interview_complete.is_set():
                                logger.info("Gemini connection closed after interview complete (expected).")
                            else:
                                logger.info(
                                    "Gemini connection closed mid-interview (code=%s reason=%s) — will reconnect.",
                                    exc.code, exc.reason,
                                )
                        except Exception as exc:
                            logger.exception("Error receiving from Gemini: %s", exc)
                        finally:
                            # Flush any partial transcript buffered in state.
                            if _is_interview_mode() and not state["in_warmup"]:
                                logger.info(
                                    "[%s] Gemini session end flush: assessment_id=%s q=%s/%s len=%s",
                                    session_id,
                                    assessment_id,
                                    state["current_q"] + 1,
                                    len(response_fields),
                                    len((state["transcript"] or "").strip()),
                                )
                                _persist_current("gemini_session_end_flush")

                    # ── Run sub-tasks ─────────────────────────────────────────
                    pump_task = asyncio.create_task(pump_audio_to_gemini(), name="pump->gemini")
                    recv_task = asyncio.create_task(receive_from_gemini(), name="gemini->client")

                    done, pending = await asyncio.wait(
                        [pump_task, recv_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    if recv_task in done or client_disconnected.is_set():
                        # Either the Gemini session ended or the client left —
                        # cancel the remaining task immediately.
                        for t in pending:
                            t.cancel()
                    else:
                        # pump_task finished first but client is still connected:
                        # let recv_task drain so we don't miss the final
                        # input_transcription.finished event.
                        try:
                            await asyncio.wait_for(recv_task, timeout=5.0)
                        except asyncio.TimeoutError:
                            logger.warning("recv_task did not finish within grace period — cancelling.")
                            recv_task.cancel()

                    # Await all to surface any exceptions.
                    for t in list(done) + list(pending):
                        try:
                            await t
                        except (asyncio.CancelledError, Exception):
                            pass

            except socket.gaierror as exc:
                logger.exception("Gemini network error (will retry): %s", exc)
                try:
                    await websocket.send_json({"warning": "Gemini network error, retrying…"})
                except Exception:
                    pass
            except Exception as exc:
                logger.exception("Gemini session error (will retry): %s", exc)
                try:
                    await websocket.send_json({"warning": f"Gemini session error, retrying: {exc}"})
                except Exception:
                    pass

            if client_disconnected.is_set():
                logger.info("Client disconnected — stopping Gemini loop.")
                break
            if interview_complete.is_set():
                logger.info("Interview complete — stopping Gemini loop.")
                break

            reconnect_count += 1

        logger.info(
            "[%s] gemini_session_loop done: client_disconnected=%s interview_complete=%s answered=%s/%s assessment_id=%s",
            session_id,
            client_disconnected.is_set(),
            interview_complete.is_set(),
            state["current_q"],
            len(response_fields),
            assessment_id,
        )

    # ── Orchestrate all top-level tasks ───────────────────────────────────────
    try:
        await asyncio.gather(
            asyncio.create_task(read_client_audio(), name="read-client"),
            asyncio.create_task(gemini_session_loop(), name="gemini-loop"),
            asyncio.create_task(keepalive_client(), name="keepalive-client"),
            asyncio.create_task(autosave_answers(), name="autosave-answers"),
            return_exceptions=True,
        )
    except Exception as exc:
        logger.exception("Top-level WebSocket handler error: %s", exc)
        try:
            await websocket.send_json({"error": str(exc)})
        except Exception:
            pass
    finally:
        try:
            if not client_disconnected.is_set():
                await websocket.close()
        except Exception:
            pass