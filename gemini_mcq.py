"""
ATLAS MCQ BOT - Gemini MCQ Generator (v2.0)
============================================
Changes from v1:
  - generate_from_image / generate_from_text accept prompt_override= and count=
    so callers (bot.py) can pass the DB-selected prompt per MCQ type.
  - All else unchanged: key rotation, thinkingBudget=1024, JSON parse, validation.
"""

import json
import base64
import time
import asyncio
import httpx
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import os

from config import (
    GEMINI_KEYS, GEMINI_MODEL, GEMINI_MAX_TOKENS, GEMINI_TEMPERATURE,
    GEMINI_PROMPT, MAX_MCQ, MIN_MCQ, LOG_DIR,
)

# ============================================
# LOGGING
# ============================================
LOG_FILE = os.path.join(LOG_DIR, f"gemini_{datetime.now().strftime('%Y-%m-%d')}.log")


def log(message, level="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{timestamp}] [{level}] [GEMINI] {message}"
    print(log_msg)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_msg + "\n")
    except Exception:
        pass


def log_error(message): log(message, "ERROR")
def log_success(message): log(message, "SUCCESS")
def log_warning(message): log(message, "WARNING")


# ============================================
# GEMINI KEY MANAGER  (round-robin with cooldown)
# ============================================
class GeminiKeyManager:
    def __init__(self, keys: List[str]):
        self.keys = keys
        self.total_keys = len(keys)
        self.current_index = 0
        self.failed_keys: set = set()
        self.key_usage_count: Dict[str, int] = {k: 0 for k in keys}
        self.key_last_used: Dict[str, float] = {k: 0.0 for k in keys}
        self.cooldown_seconds = 5
        log(f"🔑 Key Manager: {self.total_keys} keys")

    def get_key(self) -> Optional[str]:
        if not self.keys:
            log_error("❌ No API keys configured")
            return None
        for _ in range(self.total_keys):
            key = self.keys[self.current_index]
            self.current_index = (self.current_index + 1) % self.total_keys
            if key in self.failed_keys:
                if time.time() - self.key_last_used.get(key, 0) > self.cooldown_seconds:
                    self.failed_keys.discard(key)
                    log(f"🔄 Key retry after cooldown: {key[:10]}...")
                else:
                    continue
            self.key_usage_count[key] = self.key_usage_count.get(key, 0) + 1
            self.key_last_used[key] = time.time()
            log(f"🔑 Using key: {key[:10]}... (used {self.key_usage_count[key]}x)")
            return key
        # All in cooldown — reset and use first
        log_warning("⚠️ All keys in cooldown, resetting...")
        self.failed_keys.clear()
        key = self.keys[0]
        self.key_usage_count[key] = self.key_usage_count.get(key, 0) + 1
        self.key_last_used[key] = time.time()
        return key

    def mark_failed(self, key: str):
        self.failed_keys.add(key)
        self.key_last_used[key] = time.time()
        log_warning(f"⚠️ Key failed: {key[:10]}...")

    def mark_success(self, key: str):
        self.failed_keys.discard(key)

    def get_stats(self) -> Dict:
        return {
            'total_keys': self.total_keys,
            'failed_keys': len(self.failed_keys),
            'active_keys': self.total_keys - len(self.failed_keys),
        }


# ============================================
# GEMINI MCQ GENERATOR
# ============================================
class GeminiMCQGenerator:
    def __init__(self, keys: List[str]):
        self.key_manager = GeminiKeyManager(keys)
        self.model = GEMINI_MODEL
        log(f"🤖 Gemini Generator: model={self.model}")

    # ------------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------------
    async def generate_from_text(
        self,
        text: str,
        prompt_override: Optional[str] = None,
        count: Optional[int] = None,
    ) -> Tuple[List[Dict], Optional[str]]:
        """
        Generate MCQs from plain text.
        prompt_override: use this prompt instead of the default (DB-selected prompt).
        count: if given, ask for exactly this many MCQs (e.g. 15 for New Exam).
        """
        log(f"📝 Generating from text ({len(text)} chars, count={count})")
        system_prompt = prompt_override or GEMINI_PROMPT
        count_instr = (
            f"\n\nGenerate exactly {count} MCQs."
            if count
            else f"\n\nGenerate maximum possible MCQs (between {MIN_MCQ} and {MAX_MCQ})."
        )
        full_prompt = text + count_instr
        mcqs, error = await self._call_gemini(full_prompt, system_prompt=system_prompt, image_data=None)
        if error:
            log_error(f"❌ Text MCQ failed: {error}")
            return [], error
        log_success(f"✅ Generated {len(mcqs)} MCQs from text")
        return mcqs, None

    async def generate_from_image(
        self,
        image_data: bytes,
        prompt_override: Optional[str] = None,
        count: Optional[int] = None,
    ) -> Tuple[List[Dict], Optional[str]]:
        """
        Generate MCQs from image bytes.
        prompt_override: use this prompt instead of the default (DB-selected prompt).
        count: if given, ask for exactly this many MCQs (e.g. 15 for New Exam).
        """
        log(f"🖼️ Generating from image ({len(image_data)} bytes, count={count})")
        system_prompt = prompt_override or GEMINI_PROMPT
        count_instr = (
            f"\n\nGenerate exactly {count} MCQs."
            if count
            else f"\n\nGenerate maximum possible MCQs (between {MIN_MCQ} and {MAX_MCQ})."
        )
        mcqs, error = await self._call_gemini(count_instr, system_prompt=system_prompt, image_data=image_data)
        if error:
            log_error(f"❌ Image MCQ failed: {error}")
            return [], error
        log_success(f"✅ Generated {len(mcqs)} MCQs from image")
        return mcqs, None

    # ------------------------------------------------------------------
    # INTERNAL — API call with key rotation
    # ------------------------------------------------------------------
    async def _call_gemini(
        self,
        text_prompt: str,
        system_prompt: str,
        image_data: Optional[bytes],
    ) -> Tuple[List[Dict], Optional[str]]:
        max_retries = max(len(GEMINI_KEYS) * 2, 4)
        for attempt in range(max_retries):
            api_key = self.key_manager.get_key()
            if not api_key:
                return [], "No API keys available"
            try:
                log(f"📡 API attempt {attempt + 1}/{max_retries}")
                result = await self._make_api_request(api_key, text_prompt, system_prompt, image_data)
                if result:
                    mcqs = self._parse_response(result)
                    if mcqs:
                        self.key_manager.mark_success(api_key)
                        return mcqs, None
                    log_warning("⚠️ Empty parse result, retrying...")
                    self.key_manager.mark_failed(api_key)
                else:
                    log_warning("⚠️ Empty API response, retrying...")
                    self.key_manager.mark_failed(api_key)
            except Exception as e:
                err = str(e).lower()
                if '429' in err or 'quota' in err or 'exhausted' in err:
                    log_warning(f"⚠️ Quota exhausted: {api_key[:10]}...")
                    self.key_manager.mark_failed(api_key)
                elif '403' in err or 'invalid' in err or 'unauthorized' in err:
                    log_error(f"❌ Invalid key: {api_key[:10]}...")
                    self.key_manager.mark_failed(api_key)
                elif '500' in err or '503' in err or 'timeout' in err:
                    log_warning("⚠️ Server error, waiting 2s...")
                    await asyncio.sleep(2)
                else:
                    log_error(f"❌ API error: {str(e)[:120]}")
                    self.key_manager.mark_failed(api_key)
        return [], "All API keys exhausted or failed"

    async def _make_api_request(
        self,
        api_key: str,
        text_prompt: str,
        system_prompt: str,
        image_data: Optional[bytes],
    ) -> Optional[Dict]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        combined = system_prompt + "\n\n" + text_prompt
        if image_data:
            parts = [
                {"inline_data": {"mime_type": self._detect_mime(image_data), "data": base64.b64encode(image_data).decode()}},
                {"text": combined},
            ]
        else:
            parts = [{"text": combined}]

        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 4096,
                "thinkingConfig": {"thinkingBudget": 1024},
                "topP": 0.95,
                "topK": 40,
            },
            "safetySettings": [
                {"category": c, "threshold": "BLOCK_NONE"}
                for c in [
                    "HARM_CATEGORY_HARASSMENT",
                    "HARM_CATEGORY_HATE_SPEECH",
                    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "HARM_CATEGORY_DANGEROUS_CONTENT",
                ]
            ],
        }
        timeout = httpx.Timeout(180.0, connect=30.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{url}?key={api_key}",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
        if response.status_code == 200:
            return response.json()
        error_data = response.json() if response.text else {}
        error_msg = error_data.get('error', {}).get('message', response.text[:200])
        raise Exception(f"API {response.status_code}: {error_msg}")

    # ------------------------------------------------------------------
    # RESPONSE PARSING
    # ------------------------------------------------------------------
    def _parse_response(self, response: Dict) -> List[Dict]:
        try:
            candidates = response.get('candidates', [])
            if not candidates:
                log_error("❌ No candidates in response")
                return []
            text = ""
            for c in candidates:
                for part in c.get('content', {}).get('parts', []):
                    text += part.get('text', '')
            if not text:
                log_error("❌ Empty text in response")
                return []
            json_text = self._extract_json(text)
            if not json_text:
                log_error("❌ No JSON in response")
                return []
            data = json.loads(json_text)
            mcqs = []
            if isinstance(data, dict):
                for key in ('mcqs', 'questions', 'quiz'):
                    if key in data and isinstance(data[key], list):
                        mcqs = data[key]
                        break
                if not mcqs:
                    for v in data.values():
                        if isinstance(v, list):
                            mcqs = v
                            break
            elif isinstance(data, list):
                mcqs = data
            valid = [m for m in (self._validate_mcq(m) for m in mcqs) if m]
            log(f"📊 Parsed {len(valid)} valid MCQs")
            return valid
        except json.JSONDecodeError as e:
            log_error(f"❌ JSON parse error: {str(e)}")
            return []
        except Exception as e:
            log_error(f"❌ Parse error: {str(e)}")
            return []

    def _extract_json(self, text: str) -> Optional[str]:
        if '```json' in text:
            text = text.split('```json')[1].split('```')[0].strip()
        elif '```' in text:
            parts = text.split('```')
            if len(parts) > 1:
                text = parts[1].strip()
        text = text.strip()
        start = next((i for i, c in enumerate(text) if c in '{['), -1)
        if start == -1:
            return None
        bracket = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            c = text[i]
            if esc:
                esc = False
                continue
            if c == '\\':
                esc = True
                continue
            if c == '"':
                in_str = not in_str
                continue
            if not in_str:
                if c in '{[':
                    bracket += 1
                elif c in '}]':
                    bracket -= 1
                    if bracket == 0:
                        return text[start:i + 1]
        return None

    def _validate_mcq(self, mcq: Dict) -> Optional[Dict]:
        try:
            question = str(mcq.get('question', '')).strip()
            if not question:
                return None
            options = mcq.get('options', [])
            if not isinstance(options, list) or len(options) < 2:
                return None
            clean = [str(o).strip() for o in options[:4] if str(o).strip()]
            if len(clean) < 2:
                return None
            while len(clean) < 4:
                clean.append('—')
            answer = mcq.get('answer', 0)
            if not isinstance(answer, int) or answer < 0 or answer >= len(clean):
                answer = 0
            exp = str(mcq.get('explanation', '')).strip()
            if len(exp) > 200:
                exp = exp[:197] + '...'
            return {
                'question': question,
                'options': clean,
                'answer': answer,
                'explanation': exp or 'ব্যাখ্যা পাওয়া যায়নি',
            }
        except Exception as e:
            log_error(f"❌ MCQ validation error: {str(e)}")
            return None

    def _detect_mime(self, data: bytes) -> str:
        if data[:4] == b'\x89PNG':
            return "image/png"
        if data[:4] == b'RIFF':
            return "image/webp"
        return "image/jpeg"


# ============================================
# HELPERS (kept for backwards compat)
# ============================================
async def download_image(file_url: str, bot_token: str) -> Optional[bytes]:
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.get(file_url)
            return r.content if r.status_code == 200 else None
    except Exception as e:
        log_error(f"❌ download_image error: {str(e)}")
        return None


async def get_file_url(file_id: str, bot_token: str) -> Optional[str]:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{bot_token}/getFile",
                json={"file_id": file_id},
            )
            d = r.json()
            if d.get('ok'):
                return f"https://api.telegram.org/file/bot{bot_token}/{d['result']['file_path']}"
    except Exception as e:
        log_error(f"❌ get_file_url error: {str(e)}")
    return None


# ============================================
# GLOBAL INSTANCE
# ============================================
mcq_generator = GeminiMCQGenerator(GEMINI_KEYS)
log("✅ Gemini MCQ Generator module initialized")
