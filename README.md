# Realtime-Transcriber (FastAPI + Faster-Whisper)

Low-latency speech-to-text service with **HTTP chunk uploads** and **WebSocket streaming**. Accepts typical browser audio (WebM/Opus, OGG, WAV, MP3, etc.), converts to 16 kHz mono WAV via `ffmpeg`, transcribes using **faster-whisper**, exposes Prometheus metrics, and ships with a simple in-browser recorder UI.

## ✨ Features

* **Two ingestion modes**
  * **HTTP**: 3–5 s audio chunks to `POST /webhook/audio`
  * **WebSocket**: binary audio chunks to `WS /ws/transcribe` for snappier updates
* **Robust audio pipeline**: tolerant MIME checks, `ffmpeg` conversion to 16 kHz mono PCM WAV
* **Production basics**: CORS, request size limits, structured errors & logs
* **Observability**: Prometheus metrics at `/metrics`, health at `/health`, version at `/version`
* **Static UI**: `static/recorder.html` with buttons for HTTP and WS recording
* **Containerized**: slim Docker image with `ffmpeg` preinstalled
* **Config via `.env`** using pydantic-settings

## 🗂️ Project Structure

```
Realtime-Transcriber/
├─ app/
│  ├─ __init__.py
│  ├─ main.py                  # FastAPI app: HTTP + WS endpoints, metrics, static mount
│  ├─ core/
│  │  ├─ config.py             # Settings (pydantic-settings)
│  │  └─ logging.py            # Console logging setup
│  └─ utils/
│     └─ audio.py              # ffmpeg conversion, temp I/O, MIME helpers
├─ static/
│  └─ recorder.html            # Browser recorder (HTTP + WS)
├─ requirements.txt
├─ Dockerfile
└─ .env                        # (optional) environment config
```

## Requirements

* **Python** 3.11+
* **ffmpeg** installed and on PATH (Docker image already includes it.)
* **Dependencies** from `requirements.txt`
* (Optional) **Docker** 24+

## 🔧 Configuration

Create `.env` (or set env vars another way):

```env
ENV=production
WHISPER_MODEL=base.en          # e.g., base.en, medium.en
COMPUTE_TYPE=int8              # CPU: int8 ; GPU builds: float16
MAX_UPLOAD_MB=25
RATELIMIT_RPM=120
# CORS_ALLOW_ORIGINS=["http://localhost:5173","http://localhost:3000"]
```

## Run Locally

1. Ensure `ffmpeg` is installed (must print a version when you run it).
2. Install Python deps:

```bash
pip install -r requirements.txt
```

3. Start the API:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

4. Open the recorder UI in your browser:
```
http://localhost:8000/static/recorder.html
```

Use Start Recording (HTTP) or Start WS to see live transcripts appear.

API docs are available at http://localhost:8000/docs.

## 🐳 Run with Docker

Build and run:
```bash
docker build -t realtime-stt .
docker run --rm -p 8000:8000 --name stt realtime-stt
```

Open the recorder UI:
```
http://localhost:8000/static/recorder.html
```

## 🔌 API Overview

### POST /webhook/audio

- Form field: `file` (audio blob)
- Optional query: `language`, `initial_prompt`
- Response: JSON with `language`, `duration`, `transcript`, `segments[]` (with `start`, `end`, `text`)

### WS /ws/transcribe

- Optional first text frame: `{"language":"en","initial_prompt":"..."}`
- Binary frames: audio chunks (WebM/OGG/WAV/MP3/etc.)
- Server replies: JSON with `ok`, `transcript`, `segments[]`, `duration`, `language`

### Meta

- `GET /health` → `{status, version, env}`
- `GET /version` → `{name, version}`
- `GET /metrics` → Prometheus exposition format (text/plain)

## 📊 Metrics (what you'll see)

### Counters

- `stt_requests_total{transport="http|ws"}` — total HTTP requests and WS chunks processed
- `stt_errors_total` — total errors

### Histogram

- `stt_request_duration_seconds` — per request/chunk duration distribution

### Process/runtime

Memory (RSS), CPU seconds, Open FDs, Python GC stats, etc.

Open http://localhost:8000/metrics to inspect current values.

## ⚡ Performance Tips

**Latency:**
- Use smaller chunks (e.g., 2–3 s) in the recorder UI
- Lower `beam_size` (e.g., 1–3) for faster inference
- Use a GPU build with `compute_type="float16"` if available

**Throughput:**
- Run multiple replicas behind a reverse proxy
- Pin one model per process; avoid reloading the model per request

## 🛡️ Security & Limits

- **CORS**: default allows common localhost dev ports; restrict in production.
- **Request size**: enforced via middleware using `MAX_UPLOAD_MB`.
- **Rate limiting**: applied to the HTTP route; WS limits can be added per-message if needed.

## 🐞 Troubleshooting

- **"ffmpeg is required but not found"** → install ffmpeg or use the Docker image.
- **Unsupported media type (415)** → the server accepts most audio/*; if conversion fails, the bytes were likely invalid. Ensure the recorder uses WebM/Opus or OGG/Opus.
- **WS connects but no text** → check browser mic permission and confirm page origin is the same as the API host/port.

## 📄 License

Add your preferred license (e.g., MIT) to a LICENSE file.
