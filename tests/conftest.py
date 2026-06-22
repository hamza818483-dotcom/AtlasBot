"""Shared test fixtures and configuration."""
import os
import sys

# Ensure the repo root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set dummy env vars before importing modules that read them at import time
os.environ.setdefault("BOT_TOKEN", "fake-token")
os.environ.setdefault("GEMINI_KEY", "")
os.environ.setdefault("OWNER_ID", "12345")
os.environ.setdefault("CF_D1_URL", "")
os.environ.setdefault("CF_D1_TOKEN", "")
