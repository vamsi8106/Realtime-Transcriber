import os
import time
import logging
import asyncio
import json
import tempfile
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocketState

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.status import HTTP_413_REQUEST_ENTITY_TOO_LARGE

from faster_whisper import WhisperModel
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.core.logging import setup_logging
from app.utils.audio import (
    ensure_ffmpeg_available,
    save_upload_to_temp,
    convert_to_wav_16k_mono,
    cleanup_paths,
    SUPPORTED_RAW_TYPES,       # still used for reference/logging
    content_type_ok,           # <-- tolerant MIME check
    normalize_content_type,    # <-- for logging
)

# ---------------------------
# Logging & metrics
# ---------------------------
setup_logging()
logger = logging.getLogger("stt-service")

REQUEST_TIME = Histogram("stt_request_duration_seconds", "Transcription request duration (s)")
REQUEST_COUNTER = Counter("stt_requests_total", "Total transcription requests", ["transport"])
ERROR_COUNTER = Counter("stt_errors_total", "Total errors")

# Rate limiting
limiter = Limiter(key_func=get_remote_address, default_limits=[f"{settings.RATELIMIT_RPM}/minute"])

# ---------------------------
# App init
# ---------------------------
app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION)

# CORS (tighten for prod)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOW_ORIGINS or ["http://localhost:5173", "http://localhost:3000", "http://localhost:8080"],
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=settings.CORS_ALLOW_METHODS,
    allow_headers=settings.CORS_ALLOW_HEADERS,
)

# Size limit middleware
class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_body_size_mb: int):
        super().__init__(app)
        self.max = max_body_size_mb * 1024 * 1024

    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > self.max:
            return JSONResponse(
                {"detail": f"Request too large. Max {settings.MAX_UPLOAD_MB} MB"},
                status_code=HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        return await call_next(request)

app.add_middleware(BodySizeLimitMiddleware, max_body_size_mb=settings.MAX_UPLOAD_MB)

# Rate limit error handler
@app.exception_handler(RateLimitExceeded)
def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Please slow down."},
    )

# Whisper model singleton
MODEL: Optional[WhisperModel] = None

@app.on_event("startup")
def startup_event():
    global MODEL
    ensure_ffmpeg_available()
    logger.info("Loading Whisper model '%s' with compute_type=%s", settings.WHISPER_MODEL, settings.COMPUTE_TYPE)
    t0 = time.time()
    MODEL = WhisperModel(settings.WHISPER_MODEL, compute_type=settings.COMPUTE_TYPE)
    logger.info("Model loaded in %.2fs", time.time() - t0)

@app.on_event("shutdown")
def shutdown_event():
    logger.info("Shutting down…")

# ---------------------------
# Health & metrics
# ---------------------------
@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "version": settings.APP_VERSION, "env": settings.ENV}

@app.get("/version", tags=["meta"])
def version():
    return {"name": settings.APP_NAME, "version": settings.APP_VERSION}

@app.get("/metrics", tags=["meta"])
def metrics():
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

# ---------------------------
# Errors
# ---------------------------
@app.exception_handler(Exception)
async def unhandled_exc(request: Request, exc: Exception):
    ERROR_COUNTER.inc()
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})

# ---------------------------
# Transcription endpoint (HTTP)
# ---------------------------
@app.post("/webhook/audio", tags=["stt"])
@limiter.limit(f"{settings.RATELIMIT_RPM}/minute")
async def transcribe_audio(
    request: Request,
    file: UploadFile = File(...),
    language: Optional[str] = None,
    initial_prompt: Optional[str] = None,
):
    """
    Accepts browser audio (webm/ogg/wav/mp3/etc), converts to 16k mono WAV, then transcribes.
    Returns transcript + segment timestamps.
    """
    REQUEST_COUNTER.labels(transport="http").inc()
    t0 = time.perf_counter()

    # Be lenient: many browsers send 'audio/webm;codecs=opus'
    if not content_type_ok(file.content_type):
        logger.warning("Suspicious content-type %s; will attempt ffmpeg convert anyway", file.content_type)

    if MODEL is None:
        raise HTTPException(status_code=503, detail="Model not ready")

    # Persist uploaded file to temp
    base_suffix = ""
    if file.filename and "." in file.filename:
        base_suffix = "." + file.filename.split(".")[-1]
    in_path = out_wav = None

    try:
        in_path = save_upload_to_temp(file, suffix=base_suffix)

        # Convert everything to 16k mono pcm wav
        try:
            out_wav, _ = convert_to_wav_16k_mono(in_path)
        except RuntimeError as conv_err:
            ct = normalize_content_type(file.content_type)
            logger.warning("Conversion failed for content-type '%s': %s", ct, conv_err)
            raise HTTPException(status_code=415, detail="Unsupported or invalid audio format") from conv_err

        # Transcribe
        segments, info = MODEL.transcribe(
            out_wav,
            language=language,                 # None -> auto
            vad_filter=True,
            beam_size=5,
            condition_on_previous_text=False,
            initial_prompt=initial_prompt,
        )

        parts = []
        segs_out = []
        for seg in segments:
            txt = seg.text.strip()
            if txt:
                parts.append(txt)
                segs_out.append({
                    "start": seg.start,
                    "end": seg.end,
                    "text": txt
                })

        transcript = " ".join(parts).strip()
        return {
            "language": getattr(info, "language", language),
            "duration": getattr(info, "duration", None),
            "transcript": transcript,
            "segments": segs_out
        }

    except HTTPException:
        raise
    except Exception as e:
        ERROR_COUNTER.inc()
        logger.exception("Transcription failed: %s", e)
        raise HTTPException(status_code=400, detail=f"Transcription failed: {e}")
    finally:
        REQUEST_TIME.observe(time.perf_counter() - t0)
        cleanup_paths(in_path, out_wav)

# ---------------------------
# WebSocket streaming endpoint
# ---------------------------
@app.websocket("/ws/transcribe")
async def ws_transcribe(websocket: WebSocket):
    """
    WebSocket that accepts binary audio chunks (webm/ogg/m4a/mp3/etc).
    Optionally send a small JSON text frame first:
      {"language": "en", "initial_prompt": "context..."}
    Then send binary audio frames; the server responds with JSON transcripts.
    """
    await websocket.accept()
    language: Optional[str] = None
    initial_prompt: Optional[str] = None

    # Try to read an optional initial JSON config quickly
    try:
        msg = await asyncio.wait_for(websocket.receive_text(), timeout=0.05)
        try:
            cfg = json.loads(msg)
            language = cfg.get("language")
            initial_prompt = cfg.get("initial_prompt")
        except Exception:
            pass
    except asyncio.TimeoutError:
        pass
    except WebSocketDisconnect:
        return
    except Exception:
        pass

    if MODEL is None:
        await websocket.send_text(json.dumps({"ok": False, "error": "Model not ready"}))
        await websocket.close(code=1013)
        return

    try:
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                break

            # Binary frames = audio chunks
            if "bytes" in msg and msg["bytes"] is not None:
                REQUEST_COUNTER.labels(transport="ws").inc()
                raw = msg["bytes"]
                in_path = out_wav = None
                t0 = time.perf_counter()
                try:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as tmp:
                        tmp.write(raw)
                        in_path = tmp.name

                    out_wav, _ = convert_to_wav_16k_mono(in_path)

                    segments, info = MODEL.transcribe(
                        out_wav,
                        language=language,
                        vad_filter=True,
                        beam_size=5,
                        condition_on_previous_text=False,
                        initial_prompt=initial_prompt,
                    )

                    parts, segs_out = [], []
                    for seg in segments:
                        t = seg.text.strip()
                        if t:
                            parts.append(t)
                            segs_out.append({"start": seg.start, "end": seg.end, "text": t})

                    await websocket.send_text(json.dumps({
                        "ok": True,
                        "transcript": " ".join(parts).strip(),
                        "segments": segs_out,
                        "duration": getattr(info, "duration", None),
                        "language": getattr(info, "language", language),
                    }))
                except Exception as e:
                    await websocket.send_text(json.dumps({"ok": False, "error": str(e)}))
                finally:
                    REQUEST_TIME.observe(time.perf_counter() - t0)
                    cleanup_paths(in_path, out_wav)

            # Optional text frames to update config mid-session
            elif "text" in msg and msg["text"] is not None:
                try:
                    data = json.loads(msg["text"])
                    if "language" in data:
                        language = data["language"]
                    if "initial_prompt" in data:
                        initial_prompt = data["initial_prompt"]
                    await websocket.send_text(json.dumps({"ok": True, "msg": "config updated"}))
                except Exception:
                    await websocket.send_text(json.dumps({"ok": False, "error": "invalid text frame"}))

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            if websocket.application_state == WebSocketState.CONNECTED:
                await websocket.send_text(json.dumps({"ok": False, "error": f"server error: {e}"}))
        finally:
            try:
                await websocket.close()
            except Exception:
                pass

# ---------------------------
# Static files (serve /static/recorder.html)
# ---------------------------
app.mount(
    "/static",
    StaticFiles(directory=os.path.join(os.path.dirname(__file__), "..", "static")),
    name="static",
)
