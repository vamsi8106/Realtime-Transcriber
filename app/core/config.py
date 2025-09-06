# app/core/config.py
from typing import List
from pydantic_settings import BaseSettings
from pydantic import AnyHttpUrl

class Settings(BaseSettings):
    APP_NAME: str = "Whisper STT Service"
    APP_VERSION: str = "1.0.0"
    ENV: str = "production"

    # Faster-Whisper
    WHISPER_MODEL: str = "base.en"
    COMPUTE_TYPE: str = "int8"

    # Server
    MAX_UPLOAD_MB: int = 25
    CORS_ALLOW_ORIGINS: List[AnyHttpUrl] = []
    CORS_ALLOW_CREDENTIALS: bool = False
    CORS_ALLOW_METHODS: List[str] = ["POST", "GET", "OPTIONS"]
    CORS_ALLOW_HEADERS: List[str] = ["*"]

    # Rate limiting
    RATELIMIT_RPM: int = 60

    class Config:
        env_file = ".env"

settings = Settings()
