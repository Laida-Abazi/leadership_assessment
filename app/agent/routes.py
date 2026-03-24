"""
Voice agent routes: Gemini Live (STS) WebSocket proxy for friendly conversation
and structured assessment interviews.
"""
import asyncio
import logging
import os
import re
import socket
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
# Override with env GEMINI_LIVE_MODEL if your API supports a different model.
LIVE_MODEL = os.environ.get(
    "GEMINI_LIVE_MODEL",
    "gemini-2.5-flash-native-audio-preview-12-2025",
)

# Agent voice: one of the prebuilt names (e.g. Puck, Zephyr, Aoede, Kore, Charon, Leda).
# Override with env GEMINI_LIVE_VOICE. Full list: see Vertex AI "Configure language and voice" docs.
AGENT_VOICE = os.environ.get("GEMINI_LIVE_VOICE", "Puck")

# Assessment question columns in order (must match Assessments model and assessment routes).
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

DEFAULT_SYSTEM_INSTRUCTION = """You are a friendly, warm voice assistant. Have a natural, conversational chat with the user. Keep responses concise and conversational so they work well in a voice dialogue. Be helpful and personable."""


# Response table columns in same order as ASSESSMENT_QUESTION_FIELDS (question -> response).
RESPONSE_FIELDS = [f.replace("_question", "_response") for f in ASSESSMENT_QUESTION_FIELDS]


def _assessment_questions_list(assessment: Assessments) -> list[str]:
    """Return non-empty assessment questions in the canonical order."""
    out: list[str] = []
    for field in ASSESSMENT_QUESTION_FIELDS:
        val = getattr(assessment, field, None)
        if val and isinstance(val, str) and val.strip():
            out.append(val.strip())
    return out


def _response_fields_for_assessment(assessment: Assessments) -> list[str]:
    """Return response column names in the same order as questions we ask (only for non-empty questions)."""
    out: list[str] = []
    for field in ASSESSMENT_QUESTION_FIELDS:
        val = getattr(assessment, field, None)
        if val and isinstance(val, str) and val.strip():
            out.append(field.replace("_question", "_response"))
    return out


def _save_response_for_question(
    assessment_id: int,
    response_field_order: list[str],
    current_index: int,
    answer_text: str,
) -> bool:
    """Get or create Responses row for assessment and set the given question index to answer_text."""
    if current_index < 0 or current_index >= len(response_field_order):
        return False
    field_name = response_field_order[current_index]
    db = SessionLocal()
    try:
        existing = db.query(Responses).filter(Responses.assessment_id == assessment_id).first()
        if existing:
            row = existing
        else:
            row = Responses(assessment_id=assessment_id)
            db.add(row)
            db.flush()
        if hasattr(row, field_name):
            setattr(row, field_name, (answer_text or "").strip() or None)
        db.commit()
        logger.info("Saved response for assessment_id=%s field=%s index=%s", assessment_id, field_name, current_index)
        return True
    except Exception as e:
        logger.exception("Failed to save response: %s", e)
        db.rollback()
        return False
    finally:
        db.close()


def _get_saved_response_count(assessment_id: int, response_field_order: list[str]) -> int:
    """Return the number of leading consecutive saved responses (for reconnection)."""
    db = SessionLocal()
    try:
        row = db.query(Responses).filter(Responses.assessment_id == assessment_id).first()
        if not row:
            return 0
        count = 0
        for field in response_field_order:
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
    start_number: int = 1,
) -> str:
    """Build system instruction for conducting the assessment interview."""
    if not questions:
        return DEFAULT_SYSTEM_INSTRUCTION
    clean_name = (candidate_name or "").strip()
    lines = [
        "You are conducting a structured leadership assessment interview as a professional, warm voice interviewer.",
        "",
        "Your role: ask the following questions ONE BY ONE, in order. Rephrase slightly only for natural, friendly speech. Wait for the candidate's full answer before moving on.",
        "",
        "Rules:",
    ]
    if resumed:
        lines.append(
            "- The interview may resume on a fresh backend session. Continue seamlessly and naturally without mentioning "
            "any interruption, reconnection, restart, or saying things like 'Welcome back'. Simply proceed with the next "
            "question below as if the conversation had been continuous."
        )
    else:
        if clean_name:
            lines.append(
                f"- Start with a warm introduction and greet them by name: '{clean_name}'. "
                "Ask how their day is going, wait for a brief response, and reassure them this is a casual conversation. "
                "Treat this opening exchange as warm-up only; after that, start the formal assessment with Question 1 in a new turn."
            )
        else:
            lines.append(
                "- Start with a warm introduction, ask how their day is going, wait for a brief response, "
                "and reassure them this is a casual conversation. Treat this opening exchange as warm-up only; "
                "after that, start the formal assessment with Question 1 in a new turn."
            )
    lines += [
        "- Start every formal assessment question with the exact spoken prefix 'Question N:' where N is the question number below.",
        "- If you need clarification for the same question, start with the exact spoken prefix 'Follow-up on Question N:'.",
        "- Only move to the next numbered question after you are satisfied the current numbered question has been answered.",
        "- Ask exactly the question text below for each step; you may rephrase slightly for natural speech only if needed.",
        "- After the candidate answers, acknowledge warmly and briefly, then ask the next question smoothly.",
        "- Sprinkle in light reassurance as needed (e.g., 'No right or wrong answers here—just your thoughts').",
        "- Do not skip questions or jump ahead. Do not repeat a question once they have answered.",
        "- Keep your own turns concise so the conversation flows well for voice.",
        "- After the last question is answered, thank them and say the interview is complete and end positively (e.g., 'You've given some fantastic insights—thanks so much!').",
        "",
        "Questions to ask (in this order):",
    ]
    for i, q in enumerate(questions, start_number):
        lines.append(f"{i}. {q}")
    return "\n".join(lines)


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
    - Query param assessment_id (optional): if set, the agent runs the assessment interview.
    - Client sends binary: raw 16-bit PCM audio at 16 kHz, mono (no header).
    - Server sends binary: raw 16-bit PCM audio at 24 kHz, mono (no header).
    """
    await websocket.accept()

    assessment_id: Optional[int] = None
    raw = websocket.query_params.get("assessment_id")
    if raw:
        try:
            assessment_id = int(raw)
        except ValueError:
            pass

    system_instruction = DEFAULT_SYSTEM_INSTRUCTION
    response_field_order: list[str] = []
    already_saved = 0
    questions = []
    candidate_name: str | None = None

    if assessment_id is not None:
        db = SessionLocal()
        try:
            assessment = db.get(Assessments, assessment_id)
            if assessment:
                user = db.get(User, assessment.user_id)
                if user:
                    candidate_name = f"{(user.name or '').strip()} {(user.surname or '').strip()}".strip() or None
                questions = _assessment_questions_list(assessment)
                response_field_order = _response_fields_for_assessment(assessment)
                already_saved = _get_saved_response_count(assessment_id, response_field_order)
                remaining_questions = questions[already_saved:]
                resumed = already_saved > 0
                if remaining_questions:
                    system_instruction = _build_interview_system_instruction(
                        remaining_questions,
                        resumed=resumed,
                        candidate_name=candidate_name,
                        start_number=already_saved + 1,
                    )
                else:
                    logger.info("All %d questions already answered for assessment_id=%s", len(questions), assessment_id)
                logger.info(
                    "Agent starting assessment interview id=%s with %s questions (%s already answered, %s remaining)",
                    assessment_id, len(questions), already_saved, len(remaining_questions),
                )
            else:
                logger.warning("Assessment id=%s not found, using default instruction", assessment_id)
        finally:
            db.close()

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.exception("google-genai not installed")
        await websocket.send_json({"error": "Server missing google-genai. Install with: pip install google-genai"})
        await websocket.close()
        return

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        await websocket.send_json({"error": "GOOGLE_API_KEY or GEMINI_API_KEY not set"})
        await websocket.close()
        return

    # ── Shared state ────────────────────────────────────────────────────────────
    client_disconnected = asyncio.Event()
    interview_complete = asyncio.Event()

    # Audio queue: client audio is buffered here and consumed by whichever
    # Gemini session is currently active (including across reconnects).
    audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=200)

    answer_state: dict = {
        "current_index": already_saved,
        "transcript": "",
        "discard_intro_reply": bool(assessment_id is not None and response_field_order and already_saved == 0),
        "question_open": False,
        "user_turn_finished": False,
        "output_transcript": "",
    }
    MIN_TRANSCRIPT_LEN = 20

    def signal_client_disconnect() -> None:
        """Mark the client as gone and wake any Gemini pump waiting on audio."""
        if client_disconnected.is_set():
            return
        client_disconnected.set()
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
                logger.warning("Could not enqueue disconnect sentinel; audio queue remained full.")

    def _build_config_for_current_state() -> dict:
        """Rebuild system instruction based on how many answers have been saved so far."""
        cfg = {
            "response_modalities": ["AUDIO"],
            "speech_config": {
                "voice_config": {
                    "prebuilt_voice_config": {"voice_name": "Orus"},
                },
            },
        }
        if assessment_id is not None and response_field_order:
            saved = answer_state["current_index"]
            remaining = questions[saved:]
            resumed = saved > 0
            if remaining:
                cfg["system_instruction"] = _build_interview_system_instruction(
                    remaining,
                    resumed=resumed,
                    candidate_name=candidate_name,
                    start_number=saved + 1,
                )
            else:
                cfg["system_instruction"] = system_instruction
            cfg["input_audio_transcription"] = types.AudioTranscriptionConfig()
            cfg["output_audio_transcription"] = types.AudioTranscriptionConfig()
        else:
            cfg["system_instruction"] = system_instruction
        return cfg

    def _finalize_current_answer(reason: str, min_length: int = 0) -> bool:
        """Persist the current answer once and block late transcript spillover."""
        transcript = (answer_state["transcript"] or "").strip()
        idx = answer_state["current_index"]
        if not transcript:
            return False
        if min_length and len(transcript) < min_length:
            return False
        if idx >= len(response_field_order):
            answer_state["transcript"] = ""
            return False

        logger.info(
            "Saving response (%s): assessment_id=%s index=%s field=%s len=%s",
            reason,
            assessment_id,
            idx,
            response_field_order[idx],
            len(transcript),
        )
        saved = _save_response_for_question(
            assessment_id,
            response_field_order,
            idx,
            transcript,
        )
        if saved:
            answer_state["current_index"] = idx + 1
            answer_state["transcript"] = ""
            answer_state["user_turn_finished"] = False
        return saved

    def _extract_question_marker(text: str) -> tuple[str | None, int | None]:
        """Parse machine-readable spoken markers like 'Question 2:' or 'Follow-up on Question 2:'."""
        if not text:
            return None, None
        match = re.search(r"\bfollow[- ]up on question\s+(\d+)\b", text, flags=re.IGNORECASE)
        if match:
            return "followup", int(match.group(1))
        match = re.search(r"\bquestion\s+(\d+)\b", text, flags=re.IGNORECASE)
        if match:
            return "question", int(match.group(1))
        return None, None

    # ── Task: read client WebSocket → audio_queue ───────────────────────────────
    async def read_client_audio():
        """Pump raw PCM bytes from the client WebSocket into audio_queue."""
        consecutive_errors = 0
        try:
            while True:
                try:
                    msg = await websocket.receive()
                except WebSocketDisconnect as e:
                    code = getattr(e, "code", 1000)
                    if code in (1000, 1001):
                        logger.info("Client closed WebSocket cleanly (code=%s).", code)
                    else:
                        logger.warning("Client WebSocket disconnected unexpectedly (code=%s).", code)
                    break
                except Exception as e:
                    consecutive_errors += 1
                    logger.warning("Transient receive error #%s: %s", consecutive_errors, e)
                    if consecutive_errors >= 5:
                        logger.error("Too many consecutive receive errors, treating as disconnect.")
                        break
                    await asyncio.sleep(0.1)
                    continue

                consecutive_errors = 0
                if msg.get("type") != "websocket.receive":
                    if msg.get("type") == "websocket.disconnect":
                        code = msg.get("code", 1000)
                        if code in (1000, 1001):
                            logger.info("Client disconnect frame received with normal close code=%s.", code)
                        else:
                            logger.warning("Client disconnect frame received with unexpected code=%s.", code)
                        break
                    continue
                data = msg.get("bytes")
                if not data:
                    continue
                try:
                    audio_queue.put_nowait(data)
                except asyncio.QueueFull:
                    # Drop oldest frame to avoid unbounded memory growth
                    try:
                        audio_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    audio_queue.put_nowait(data)
        except Exception as e:
            logger.exception("Error reading client audio: %s", e)
        finally:
            # Sentinel: tells the Gemini pump to stop.
            signal_client_disconnect()
            logger.info("Client disconnected / read_client_audio finished.")

    async def keepalive_client():
        """Send periodic JSON pings so proxies keep the client WebSocket open."""
        while not client_disconnected.is_set():
            await asyncio.sleep(20)
            if client_disconnected.is_set():
                break
            try:
                await websocket.send_json({"ping": True})
            except WebSocketDisconnect as e:
                logger.info("Client disconnected during keepalive (code=%s).", getattr(e, "code", None))
                signal_client_disconnect()
                break
            except Exception as e:
                logger.warning("Client keepalive failed; treating socket as disconnected: %s", e)
                signal_client_disconnect()
                break

    # ── Gemini session loop (reconnects on transient failures) ──────────────────
    async def gemini_session_loop():
        """
        Manages one (or more) Gemini Live sessions for the lifetime of the
        client connection.  Reconnects automatically when Gemini closes the
        session (e.g. 10-minute hard timeout) as long as:
          - the client is still connected, AND
          - the interview is not yet complete.
        """
        nonlocal system_instruction
        client = genai.Client(api_key=api_key)
        MAX_RECONNECTS = 10
        reconnect_count = 0
        RECONNECT_DELAY = 2  # seconds between reconnect attempts

        while not client_disconnected.is_set() and not interview_complete.is_set():
            if reconnect_count > 0:
                logger.info(
                    "Reconnecting to Gemini (attempt %s/%s) — resuming from question index %s",
                    reconnect_count, MAX_RECONNECTS, answer_state["current_index"],
                )
                await asyncio.sleep(RECONNECT_DELAY)
                try:
                    await websocket.send_json({"status": "reconnecting", "attempt": reconnect_count})
                except Exception:
                    pass

            if reconnect_count >= MAX_RECONNECTS:
                logger.error("Exceeded max Gemini reconnect attempts (%s). Giving up.", MAX_RECONNECTS)
                try:
                    await websocket.send_json({"error": "Could not maintain Gemini connection after multiple attempts."})
                except Exception:
                    pass
                break

            config = _build_config_for_current_state()
            _logged_sc_keys = False

            try:
                async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:
                    session_rotation_requested = False
                    # Send a silent frame immediately so Gemini doesn't time out
                    # before the client starts speaking.
                    silent_frame = b'\x00' * 3200
                    await session.send_realtime_input(
                        audio=types.Blob(data=silent_frame, mime_type="audio/pcm;rate=16000")
                    )

                    if reconnect_count == 0:
                        await websocket.send_json({"status": "connected", "model": LIVE_MODEL})
                    else:
                        await websocket.send_json({"status": "reconnected", "model": LIVE_MODEL})

                    # ── Sub-task: audio_queue → Gemini ──────────────────────────
                    async def pump_audio_to_gemini():
                        keep_alive_frame = b'\x00' * 3200
                        last_real_audio = asyncio.get_event_loop().time()
                        KEEP_ALIVE_INTERVAL = 1.0

                        while True:
                            try:
                                chunk = await asyncio.wait_for(
                                    audio_queue.get(),
                                    timeout=KEEP_ALIVE_INTERVAL,
                                )
                            except asyncio.TimeoutError:
                                # No real audio — send keep-alive silence
                                try:
                                    await session.send_realtime_input(
                                        audio=types.Blob(data=keep_alive_frame, mime_type="audio/pcm;rate=16000")
                                    )
                                except Exception as e:
                                    logger.debug("Keep-alive failed (session closed): %s", e)
                                    break
                                continue

                            if chunk is None:
                                # Sentinel: client disconnected — put it back so
                                # any future reconnect attempt also sees it.
                                await audio_queue.put(None)
                                break

                            try:
                                await session.send_realtime_input(
                                    audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                                )
                                last_real_audio = asyncio.get_event_loop().time()
                            except Exception as e:
                                logger.debug("Audio send failed (session closed): %s", e)
                                # Put the chunk back so it's not lost on reconnect
                                try:
                                    audio_queue.put_nowait(chunk)
                                except asyncio.QueueFull:
                                    pass
                                break

                

                    async def receive_from_gemini():
                        nonlocal _logged_sc_keys
                        try:
                            while not client_disconnected.is_set():
                                turn_had_messages = False
                                async for message in session.receive():
                                    turn_had_messages = True
                                    try:
                                        if not _logged_sc_keys:
                                            try:
                                                msg_dump = getattr(message, "model_dump", lambda **kw: {})(exclude_none=True)
                                                logger.info("Gemini raw message keys: %s", list(msg_dump.keys()))
                                            except Exception:
                                                logger.info(
                                                    "Gemini message type: %s attrs: %s",
                                                    type(message).__name__,
                                                    [a for a in dir(message) if not a.startswith('_')],
                                                )

                                        go_away = getattr(message, "go_away", None)
                                        if go_away is not None:
                                            logger.warning("Gemini sent go_away signal: %s", go_away)
                                            session_rotation_requested = True

                                        if not hasattr(message, "server_content") or message.server_content is None:
                                            if not _logged_sc_keys:
                                                logger.info("Gemini message without server_content: %s", type(message).__name__)
                                            continue

                                        sc = message.server_content
                                        model_turn = getattr(sc, "model_turn", None)
                                        turn_complete = getattr(sc, "turn_complete", False)
                                        out = getattr(sc, "output_transcription", None)

                                        if assessment_id is not None and not _logged_sc_keys:
                                            try:
                                                payload = getattr(sc, "model_dump", lambda **kw: {})(exclude_none=True)
                                                logger.info("Live server_content keys (first time): %s", list(payload.keys()))
                                            except Exception:
                                                pass
                                            _logged_sc_keys = True

                                        if out is not None:
                                            out_text = (getattr(out, "text", None) or "").strip()
                                            if out_text:
                                                answer_state["output_transcript"] = (
                                                    f'{answer_state["output_transcript"]} {out_text}'.strip()
                                                )

                                        # ── Transcription → save answer ─────────────────────────────
                                        if assessment_id is not None and response_field_order:
                                            inp = getattr(sc, "input_transcription", None)
                                            if inp is not None:
                                                text = getattr(inp, "text", None) or ""
                                                finished = getattr(inp, "finished", False)
                                                if answer_state["discard_intro_reply"]:
                                                    if finished:
                                                        logger.info(
                                                            "Discarded warm-up reply for assessment_id=%s before Question 1 capture.",
                                                            assessment_id,
                                                        )
                                                        answer_state["transcript"] = ""
                                                        answer_state["user_turn_finished"] = False
                                                elif text and answer_state["question_open"]:
                                                    answer_state["transcript"] = (
                                                        (answer_state["transcript"] or "") + text
                                                    ).strip()
                                                if finished and answer_state["question_open"]:
                                                    answer_state["user_turn_finished"] = True
                                                    answer_state["question_open"] = False

                                        # ── Question progression based on agent output transcription ──
                                        if assessment_id is not None and response_field_order and turn_complete:
                                            output_text = (answer_state["output_transcript"] or "").strip()
                                            marker_kind, marker_number = _extract_question_marker(output_text)
                                            current_question_number = answer_state["current_index"] + 1

                                            if answer_state["discard_intro_reply"]:
                                                if marker_kind == "question" and marker_number == current_question_number:
                                                    logger.info(
                                                        "Detected Question %s start for assessment_id=%s after warm-up.",
                                                        marker_number,
                                                        assessment_id,
                                                    )
                                                    answer_state["discard_intro_reply"] = False
                                                    answer_state["question_open"] = True
                                                    answer_state["user_turn_finished"] = False
                                                    answer_state["transcript"] = ""
                                            else:
                                                if marker_kind == "followup" and marker_number == current_question_number:
                                                    logger.info(
                                                        "Detected follow-up on Question %s for assessment_id=%s; appending to same response.",
                                                        marker_number,
                                                        assessment_id,
                                                    )
                                                    answer_state["question_open"] = True
                                                    answer_state["user_turn_finished"] = False
                                                elif marker_kind == "question" and marker_number == current_question_number:
                                                    logger.info(
                                                        "Detected Question %s prompt for assessment_id=%s; opening capture.",
                                                        marker_number,
                                                        assessment_id,
                                                    )
                                                    answer_state["question_open"] = True
                                                    answer_state["user_turn_finished"] = False
                                                elif (
                                                    marker_kind == "question"
                                                    and marker_number == current_question_number + 1
                                                ):
                                                    if answer_state["transcript"]:
                                                        _finalize_current_answer(
                                                            "question_transition",
                                                            min_length=MIN_TRANSCRIPT_LEN,
                                                        )
                                                    logger.info(
                                                        "Detected transition to Question %s for assessment_id=%s.",
                                                        marker_number,
                                                        assessment_id,
                                                    )
                                                    answer_state["question_open"] = True
                                                    answer_state["user_turn_finished"] = False
                                                elif (
                                                    answer_state["user_turn_finished"]
                                                    and answer_state["current_index"] == len(response_field_order) - 1
                                                    and answer_state["transcript"]
                                                ):
                                                    _finalize_current_answer(
                                                        "final_question_complete",
                                                        min_length=MIN_TRANSCRIPT_LEN,
                                                    )
                                                    answer_state["question_open"] = False
                                                elif answer_state["user_turn_finished"] and answer_state["transcript"]:
                                                    logger.warning(
                                                        "Could not determine next question marker after Question %s for assessment_id=%s. "
                                                        "Keeping current question open for follow-up.",
                                                        current_question_number,
                                                        assessment_id,
                                                    )
                                                    answer_state["question_open"] = True
                                                    answer_state["user_turn_finished"] = False

                                            answer_state["output_transcript"] = ""

                                        # ── Forward audio to client ─────────────────────────────────
                                        parts = None
                                        if model_turn and getattr(model_turn, "parts", None):
                                            parts = model_turn.parts
                                        if not parts and getattr(sc, "parts", None):
                                            parts = sc.parts
                                        if parts:
                                            for part in parts:
                                                inline = getattr(part, "inline_data", None)
                                                if inline and getattr(inline, "data", None):
                                                    try:
                                                        await websocket.send_bytes(inline.data)
                                                    except Exception as e:
                                                        logger.warning("Failed to send audio to client: %s", e)
                                                        signal_client_disconnect()
                                                        return

                                        if getattr(sc, "interrupted", False):
                                            try:
                                                await websocket.send_json({"interrupted": True})
                                            except Exception:
                                                signal_client_disconnect()
                                                return

                                        # ── Notify client when all answers are saved (no auto-close) ──
                                        if (
                                            assessment_id is not None
                                            and response_field_order
                                            and answer_state["current_index"] >= len(response_field_order)
                                            and turn_complete
                                            and not interview_complete.is_set()
                                        ):
                                            logger.info(
                                                "All %s responses saved for assessment_id=%s — staying connected until client ends.",
                                                len(response_field_order), assessment_id,
                                            )
                                            interview_complete.set()
                                            try:
                                                await websocket.send_json({"answers_saved": True})
                                            except Exception:
                                                pass

                                        if session_rotation_requested and turn_complete:
                                            logger.info(
                                                "Rotating Gemini session after completed turn at question index %s.",
                                                answer_state["current_index"],
                                            )
                                            return

                                    except Exception as e:
                                        logger.warning("Error handling Gemini message: %s", e)

                                if not turn_had_messages:
                                    # session.receive() returned immediately with no messages —
                                    # the underlying WebSocket is closed server-side.
                                    logger.info(
                                        "Gemini session closed server-side. answered=%s/%s",
                                        answer_state["current_index"], len(response_field_order),
                                    )
                                    break
                                # Normal turn_complete end — loop continues to receive the next turn.

                        except asyncio.CancelledError:
                            pass
                        except websockets.exceptions.ConnectionClosedError as e:
                            if interview_complete.is_set():
                                logger.info("Gemini session closed after all answers saved (expected).")
                            else:
                                logger.info(
                                    "Gemini session closed mid-interview (code=%s reason=%s) — outer loop will reconnect.",
                                    e.code, e.reason,
                                )
                        except Exception as e:
                            logger.exception("Error receiving from Gemini: %s", e)

                    pump_task = asyncio.create_task(pump_audio_to_gemini(), name="pump->gemini")
                    recv_task = asyncio.create_task(receive_from_gemini(), name="gemini->client")

                    done, pending = await asyncio.wait(
                        [pump_task, recv_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    for t in pending:
                        t.cancel()
                    for t in pending:
                        try:
                            await t
                        except asyncio.CancelledError:
                            pass

                    for t in done:
                        if not t.cancelled() and t.exception():
                            logger.warning("Session sub-task '%s' error: %s", t.get_name(), t.exception())

            except socket.gaierror as e:
                logger.exception("Gemini network error (will retry): %s", e)
                try:
                    await websocket.send_json({"warning": "Gemini network error, retrying..."})
                except Exception:
                    pass
            except Exception as e:
                logger.exception("Gemini session error (will retry): %s", e)
                try:
                    await websocket.send_json({"warning": f"Gemini session error, retrying: {e}"})
                except Exception:
                    pass

            if client_disconnected.is_set():
                logger.info("Client disconnected — not reconnecting.")
                break

            if interview_complete.is_set():
                logger.info("Interview complete — not reconnecting.")
                break

            reconnect_count += 1

        logger.info(
            "gemini_session_loop exiting. client_disconnected=%s interview_complete=%s answered=%s/%s",
            client_disconnected.is_set(),
            interview_complete.is_set(),
            answer_state["current_index"],
            len(response_field_order),
        )

    # ── Top-level task orchestration ─────────────────────────────────────────────
    try:
        top_tasks = [
            asyncio.create_task(read_client_audio(), name="read-client"),
            asyncio.create_task(gemini_session_loop(), name="gemini-loop"),
            asyncio.create_task(keepalive_client(), name="keepalive-client"),
        ]

        # Wait until BOTH the client reader AND the Gemini loop finish.
        # This means we stay alive through Gemini reconnects as long as
        # the client WebSocket is open.
        await asyncio.gather(*top_tasks, return_exceptions=True)

    except Exception as e:
        logger.exception("Top-level WebSocket handler error: %s", e)
        try:
            await websocket.send_json({"error": str(e)})
        except Exception:
            pass
    finally:
        try:
            if not client_disconnected.is_set():
                await websocket.close()
        except Exception:
            pass