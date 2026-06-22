"""
Shared Gemini client with multi-key rotation.

Both bot.py and exam_server.py maintain separate Gemini clients (since
they may run in the same process), but the setup/rotation logic is
identical and consolidated here.
"""

from typing import Optional, List

from google import genai

from shared.config import GEMINI_KEYS


class GeminiRotatingClient:
    """Wraps a genai.Client with round-robin key rotation."""

    def __init__(self, keys: Optional[List[str]] = None, label: str = ""):
        self._keys = keys or GEMINI_KEYS
        self._idx = 0
        self._client: Optional[genai.Client] = None
        self._label = label

    @property
    def client(self) -> Optional[genai.Client]:
        return self._client

    @property
    def key_count(self) -> int:
        return len(self._keys)

    @property
    def current_key_index(self) -> int:
        return self._idx

    def setup(self) -> None:
        if self._keys:
            self._client = genai.Client(api_key=self._keys[0])
            tag = f" ({self._label})" if self._label else ""
            print(f"✅ Gemini configured{tag} ({len(self._keys)} keys loaded)")
        else:
            tag = f" ({self._label})" if self._label else ""
            print(f"⚠️ No GEMINI keys!{tag}")

    def rotate(self) -> bool:
        """Rotate to the next key. Returns False if only one key available."""
        if len(self._keys) <= 1:
            return False
        self._idx = (self._idx + 1) % len(self._keys)
        try:
            self._client = genai.Client(api_key=self._keys[self._idx])
            tag = f" ({self._label})" if self._label else ""
            print(f"🔄 Rotated to key #{self._idx+1}/{len(self._keys)}{tag}")
            return True
        except Exception as e:
            print(f"rotate key failed: {e}")
            return False
