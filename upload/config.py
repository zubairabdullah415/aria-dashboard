"""
config.py — LiftUp SaaS Application Settings
"""
from typing import List
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DEBUG:          bool  = False
    SECRET_KEY:     str              # Min 32 random bytes
    PLATFORM_URL:   str  = "https://api.liftupai.com"

    DATABASE_URL:        str         # postgresql://liftup_app:pass@host:5432/liftup
    ANTHROPIC_API_KEY:   str
    SENDGRID_API_KEY:    str
    EMAIL_FROM_ADDRESS:  str = "noreply@liftupai.com"

    TWILIO_ACCOUNT_SID:       str
    TWILIO_AUTH_TOKEN:        str
    TWILIO_PLATFORM_NUMBER:   str = ""  # Fallback if restaurant has no dedicated number

    class Config:
        env_file = ".env"


settings = Settings()
