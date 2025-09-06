# app/utils/audio.py
import os
import subprocess
import tempfile
from typing import Tuple

# keep the base types; we'll allow any 'audio/*' plus parameters too
SUPPORTED_RAW_TYPES = {
    "audio/wav",
    "audio/x-wav",
    "audio/webm",
    "audio/ogg",
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/x-m4a",
    "audio/aac",
    "audio/flac",
    "application/octet-stream",  # some browsers/drivers send this
}

def normalize_content_type(ct: str | None) -> str:
    if not ct:
        return ""
    return ct.split(";", 1)[0].strip().lower()  # drop ;codecs=opus etc.

def content_type_ok(ct: str | None) -> bool:
    base = normalize_content_type(ct)
    if not base:
        return True
    # allow any audio/* even if not enumerated above; ffmpeg will be the final arbiter
    return base in SUPPORTED_RAW_TYPES or base.startswith("audio/")

def ensure_ffmpeg_available():
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except Exception as e:
        raise RuntimeError("ffmpeg is required but not found in PATH") from e

def save_upload_to_temp(upload_file, suffix: str = "") -> str:
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        for chunk in iter(lambda: upload_file.file.read(1024 * 1024), b""):
            tmp.write(chunk)
        return tmp.name

def convert_to_wav_16k_mono(in_path: str) -> Tuple[str, bool]:
    out_path = in_path + ".wav"
    cmd = [
        "ffmpeg", "-y",
        "-i", in_path,
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        out_path,
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg conversion failed: {res.stderr.decode('utf-8', 'ignore')}")
    return out_path, True

def cleanup_paths(*paths: str):
    for p in paths:
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
