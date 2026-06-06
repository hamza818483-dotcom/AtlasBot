"""
ATLAS MCQ BOT - Gemini MCQ Generator
Handles Gemini API calls, key rotation, MCQ generation from image/text
"""

import json
import base64
import time
import asyncio
import httpx
from typing import List, Dict, Optional, Tuple
from config import (
    GEMINI_KEYS, GEMINI_MODEL, GEMINI_MAX_TOKENS, GEMINI_TEMPERATURE,
    GEMINI_PROMPT, MAX_MCQ, MIN_MCQ, LOG_DIR
)
from datetime import datetime
import os

# ============================================
# LOGGING SETUP
# ============================================
LOG_FILE = os.path.join(LOG_DIR, f"gemini_{datetime.now().strftime('%Y-%m-%d')}.log")

def log(message, level="INFO"):
    """Log messages with timestamp"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{timestamp}] [{level}] [GEMINI] {message}"
    print(log_msg)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_msg + "\n")

def log_error(message):
    """Log error messages"""
    log(message, "ERROR")

def log_success(message):
    """Log success messages"""
    log(message, "SUCCESS")

def log_warning(message):
    """Log warning messages"""
    log(message, "WARNING")

# ============================================
# GEMINI KEY MANAGER (Rotation)
# ============================================
class GeminiKeyManager:
    """
    Manages multiple Gemini API keys with automatic rotation
    - Round-robin distribution
    - Failed keys temporarily skipped
    - Auto-reset when all keys exhausted
    """
    
    def __init__(self, keys: List[str]):
        self.keys = keys
        self.total_keys = len(keys)
        self.current_index = 0
        self.failed_keys = set()  # Temporarily failed keys
        self.key_usage_count = {key: 0 for key in keys}  # Track usage
        self.key_last_used = {key: 0 for key in keys}  # Track last use time
        self.cooldown_seconds = 5  # Cooldown before retrying failed key
        
        log(f"🔑 Key Manager initialized with {self.total_keys} keys")
    
    def get_key(self) -> Optional[str]:
        """
        Get next available API key using round-robin
        Skips recently failed keys
        Returns None if no keys available
        """
        if not self.keys:
            log_error("❌ No API keys configured!")
            return None
        
        # Try each key starting from current index
        attempts = 0
        while attempts < self.total_keys:
            key = self.keys[self.current_index]
            self.current_index = (self.current_index + 1) % self.total_keys
            
            # Check if key is in cooldown
            if key in self.failed_keys:
                current_time = time.time()
                if current_time - self.key_last_used.get(key, 0) > self.cooldown_seconds:
                    # Cooldown over, retry this key
                    self.failed_keys.discard(key)
                    log(f"🔄 Key retry after cooldown: {key[:10]}...")
                else:
                    attempts += 1
                    continue
            
            # Track usage
            self.key_usage_count[key] = self.key_usage_count.get(key, 0) + 1
            self.key_last_used[key] = time.time()
            
            log(f"🔑 Using key: {key[:10]}... (used {self.key_usage_count[key]} times)")
            return key
        
        # All keys failed - reset failed keys and try again
        log_warning("⚠️ All keys exhausted! Resetting failed keys...")
        self.failed_keys.clear()
        
        if self.keys:
            key = self.keys[0]
            self.key_usage_count[key] = self.key_usage_count.get(key, 0) + 1
            self.key_last_used[key] = time.time()
            log(f"🔄 Reset - using key: {key[:10]}...")
            return key
        
        return None
    
    def mark_failed(self, key: str):
        """Mark a key as temporarily failed"""
        self.failed_keys.add(key)
        self.key_last_used[key] = time.time()
        log_warning(f"⚠️ Key marked as failed: {key[:10]}... (will retry in {self.cooldown_seconds}s)")
    
    def mark_success(self, key: str):
        """Mark a key as working"""
        self.failed_keys.discard(key)
        log_success(f"✅ Key working: {key[:10]}...")
    
    def get_stats(self) -> Dict:
        """Get key usage statistics"""
        return {
            'total_keys': self.total_keys,
            'failed_keys': len(self.failed_keys),
            'usage': {k[:10]+'...': v for k, v in self.key_usage_count.items()},
            'active_keys': self.total_keys - len(self.failed_keys)
        }

# ============================================
# GEMINI MCQ GENERATOR
# ============================================
class GeminiMCQGenerator:
    """
    Handles MCQ generation from images and text using Gemini API
    Features:
    - Image/Text input support
    - Automatic key rotation
    - JSON response parsing
    - Quality validation
    - Error recovery
    """
    
    def __init__(self, keys: List[str]):
        self.key_manager = GeminiKeyManager(keys)
        self.model = GEMINI_MODEL
        self.max_tokens = GEMINI_MAX_TOKENS
        self.temperature = GEMINI_TEMPERATURE
        self.system_prompt = GEMINI_PROMPT
        log(f"🤖 Gemini Generator initialized: model={self.model}")
    
    async def generate_from_text(self, text: str, mcq_count: int = None) -> Tuple[List[Dict], Optional[str]]:
        """
        Generate MCQs from text input
        Args:
            text: Input text to generate MCQs from
            mcq_count: Desired number of MCQs (None = auto)
        Returns:
            (mcqs_list, error_message)
        """
        log(f"📝 Generating MCQs from text ({len(text)} chars)")
        
        # Build prompt
        if mcq_count:
            count_instruction = f"\n\nGenerate exactly {mcq_count} MCQs."
        else:
            count_instruction = f"\n\nGenerate maximum possible MCQs (between {MIN_MCQ} and {MAX_MCQ})."
        
        full_prompt = text + count_instruction
        
        # Call Gemini API
        mcqs, error = await self._call_gemini(full_prompt, image_data=None)
        
        if error:
            log_error(f"❌ Text MCQ generation failed: {error}")
            return [], error
        
        log_success(f"✅ Generated {len(mcqs)} MCQs from text")
        return mcqs, None
    
    async def generate_from_image(self, image_data: bytes, mcq_count: int = None) -> Tuple[List[Dict], Optional[str]]:
        """
        Generate MCQs from image input
        Args:
            image_data: Image file bytes
            mcq_count: Desired number of MCQs (None = auto)
        Returns:
            (mcqs_list, error_message)
        """
        log(f"🖼️ Generating MCQs from image ({len(image_data)} bytes)")
        
        # Build prompt
        if mcq_count:
            count_instruction = f"\n\nGenerate exactly {mcq_count} MCQs."
        else:
            count_instruction = f"\n\nGenerate maximum possible MCQs (between {MIN_MCQ} and {MAX_MCQ})."
        
        # Call Gemini API with image
        mcqs, error = await self._call_gemini(count_instruction, image_data=image_data)
        
        if error:
            log_error(f"❌ Image MCQ generation failed: {error}")
            return [], error
        
        log_success(f"✅ Generated {len(mcqs)} MCQs from image")
        return mcqs, None
    
    async def _call_gemini(self, text_prompt: str, image_data: bytes = None) -> Tuple[List[Dict], Optional[str]]:
        """
        Make API call to Gemini with key rotation and retry logic
        Args:
            text_prompt: Text prompt to send
            image_data: Optional image data for multimodal input
        Returns:
            (mcqs_list, error_message)
        """
        max_retries = len(GEMINI_KEYS) * 2  # Each key gets 2 chances
        
        for attempt in range(max_retries):
            # Get next available key
            api_key = self.key_manager.get_key()
            if not api_key:
                return [], "No API keys available"
            
            try:
                log(f"📡 API call attempt {attempt + 1}/{max_retries}")
                
                # Build the API request
                result = await self._make_api_request(api_key, text_prompt, image_data)
                
                if result:
                    # Parse and validate MCQs
                    mcqs = self._parse_response(result)
                    if mcqs and len(mcqs) >= MIN_MCQ:
                        self.key_manager.mark_success(api_key)
                        return mcqs, None
                    elif mcqs:
                        log_warning(f"⚠️ Only {len(mcqs)} MCQs generated, minimum is {MIN_MCQ}")
                        self.key_manager.mark_success(api_key)
                        return mcqs, None
                    else:
                        log_warning("⚠️ Empty response, retrying...")
                        self.key_manager.mark_failed(api_key)
                else:
                    log_warning("⚠️ Empty API response, retrying...")
                    self.key_manager.mark_failed(api_key)
                    
            except Exception as e:
                error_str = str(e).lower()
                
                # Check for specific error types
                if '429' in error_str or 'quota' in error_str or 'exhausted' in error_str:
                    log_warning(f"⚠️ Key quota exhausted: {api_key[:10]}...")
                    self.key_manager.mark_failed(api_key)
                elif '403' in error_str or 'invalid' in error_str or 'unauthorized' in error_str:
                    log_error(f"❌ Invalid key: {api_key[:10]}...")
                    self.key_manager.mark_failed(api_key)
                elif '500' in error_str or '503' in error_str or 'timeout' in error_str:
                    log_warning(f"⚠️ Server error, retrying...")
                    await asyncio.sleep(2)  # Wait before retry
                else:
                    log_error(f"❌ API error: {error_str[:100]}")
                    self.key_manager.mark_failed(api_key)
                
                continue
        
        return [], "All API keys exhausted or failed"
    
    async def _make_api_request(self, api_key: str, text_prompt: str, image_data: bytes = None) -> Optional[Dict]:
        """
        Make the actual HTTP request to Gemini API
        Supports both text-only and multimodal (image+text) requests
        """
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        
        # Build request payload
        contents = []
        
        if image_data:
            # Multimodal request (image + text)
            image_base64 = base64.b64encode(image_data).decode('utf-8')
            
            contents.append({
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": image_base64
                        }
                    },
                    {
                        "text": self.system_prompt + "\n\n" + text_prompt
                    }
                ]
            })
        else:
            # Text-only request
            contents.append({
                "parts": [
                    {
                        "text": self.system_prompt + "\n\n" + text_prompt
                    }
                ]
            })
        
        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": self.max_tokens,
                "topP": 0.95,
                "topK": 40
            },
            "safetySettings": [
                {
                    "category": "HARM_CATEGORY_HARASSMENT",
                    "threshold": "BLOCK_NONE"
                },
                {
                    "category": "HARM_CATEGORY_HATE_SPEECH",
                    "threshold": "BLOCK_NONE"
                },
                {
                    "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "threshold": "BLOCK_NONE"
                },
                {
                    "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                    "threshold": "BLOCK_NONE"
                }
            ]
        }
        
        # Make API call with timeout
        timeout = httpx.Timeout(60.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{url}?key={api_key}",
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                error_data = response.json() if response.text else {}
                error_msg = error_data.get('error', {}).get('message', response.text[:200])
                log_error(f"API error {response.status_code}: {error_msg}")
                raise Exception(f"API error {response.status_code}: {error_msg}")
    
    def _parse_response(self, response: Dict) -> List[Dict]:
        """
        Parse Gemini API response and extract MCQs
        Handles various response formats and edge cases
        """
        try:
            # Extract text from Gemini response
            candidates = response.get('candidates', [])
            if not candidates:
                log_error("❌ No candidates in response")
                return []
            
            text = ""
            for candidate in candidates:
                parts = candidate.get('content', {}).get('parts', [])
                for part in parts:
                    text += part.get('text', '')
            
            if not text:
                log_error("❌ Empty text in response")
                return []
            
            # Try to find JSON in the response
            # Gemini sometimes wraps JSON in markdown code blocks
            json_text = self._extract_json(text)
            
            if not json_text:
                log_error("❌ No JSON found in response")
                return []
            
            # Parse JSON
            data = json.loads(json_text)
            
            # Handle different JSON structures
            mcqs = []
            if isinstance(data, dict):
                if 'mcqs' in data:
                    mcqs = data['mcqs']
                elif 'questions' in data:
                    mcqs = data['questions']
                elif 'quiz' in data:
                    mcqs = data['quiz']
                else:
                    # Maybe the dict itself is MCQ data
                    for key, value in data.items():
                        if isinstance(value, list):
                            mcqs = value
                            break
            
            if isinstance(data, list):
                mcqs = data
            
            # Validate and clean MCQs
            valid_mcqs = []
            for mcq in mcqs:
                cleaned = self._validate_mcq(mcq)
                if cleaned:
                    valid_mcqs.append(cleaned)
            
            log(f"📊 Parsed {len(valid_mcqs)} valid MCQs from response")
            return valid_mcqs
            
        except json.JSONDecodeError as e:
            log_error(f"❌ JSON parse error: {str(e)}")
            log_error(f"Raw text: {text[:500]}")
            return []
        except Exception as e:
            log_error(f"❌ Response parse error: {str(e)}")
            return []
    
    def _extract_json(self, text: str) -> Optional[str]:
        """Extract JSON from text, handling markdown code blocks"""
        # Remove markdown code blocks
        if '```json' in text:
            parts = text.split('```json')
            if len(parts) > 1:
                text = parts[1].split('```')[0].strip()
        elif '```' in text:
            parts = text.split('```')
            if len(parts) > 1:
                text = parts[1].split('```')[0].strip()
        
        # Find JSON object or array
        text = text.strip()
        
        # Try to find first { or [
        start_idx = -1
        for i, char in enumerate(text):
            if char in ['{', '[']:
                start_idx = i
                break
        
        if start_idx == -1:
            return None
        
        # Extract balanced JSON
        bracket_count = 0
        is_object = text[start_idx] == '{'
        in_string = False
        escape_next = False
        
        for i in range(start_idx, len(text)):
            char = text[i]
            
            if escape_next:
                escape_next = False
                continue
            
            if char == '\\':
                escape_next = True
                continue
            
            if char == '"':
                in_string = not in_string
                continue
            
            if not in_string:
                if char in ['{', '[']:
                    bracket_count += 1
                elif char in ['}', ']']:
                    bracket_count -= 1
                    if bracket_count == 0:
                        return text[start_idx:i+1]
        
        return None
    
    def _validate_mcq(self, mcq: Dict) -> Optional[Dict]:
        """Validate and clean a single MCQ"""
        try:
            # Required fields
            question = mcq.get('question', '').strip()
            options = mcq.get('options', [])
            answer = mcq.get('answer', -1)
            explanation = mcq.get('explanation', '').strip()
            
            # Validate question exists
            if not question:
                return None
            
            # Validate options
            if not isinstance(options, list) or len(options) < 2:
                return None
            
            # Clean options (remove empty/null)
            clean_options = [opt.strip() for opt in options[:4] if opt and str(opt).strip()]
            if len(clean_options) < 2:
                return None
            
            # Pad options to exactly 4
            while len(clean_options) < 4:
                clean_options.append('—')
            
            # Validate answer index
            if not isinstance(answer, int) or answer < 0 or answer >= len(clean_options):
                answer = 0  # Default to first option
            
            # Clean explanation
            if explanation and len(explanation) > 200:
                explanation = explanation[:197] + '...'
            
            return {
                'question': question,
                'options': clean_options[:4],  # Always 4 options
                'answer': answer,
                'explanation': explanation or 'ব্যাখ্যা পাওয়া যায়নি'
            }
            
        except Exception as e:
            log_error(f"❌ MCQ validation error: {str(e)}")
            return None

# ============================================
# HELPER FUNCTIONS
# ============================================
async def download_image(file_url: str, bot_token: str) -> Optional[bytes]:
    """Download image from Telegram server"""
    log(f"📥 Downloading image from Telegram")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(file_url)
            if response.status_code == 200:
                log(f"✅ Image downloaded: {len(response.content)} bytes")
                return response.content
            else:
                log_error(f"❌ Image download failed: {response.status_code}")
                return None
    except Exception as e:
        log_error(f"❌ Image download error: {str(e)}")
        return None

async def get_file_url(file_id: str, bot_token: str) -> Optional[str]:
    """Get file download URL from Telegram"""
    try:
        url = f"https://api.telegram.org/bot{bot_token}/getFile"
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json={"file_id": file_id})
            data = response.json()
            
            if data.get('ok'):
                file_path = data['result']['file_path']
                file_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
                return file_url
    except Exception as e:
        log_error(f"❌ Get file URL error: {str(e)}")
    return None

# ============================================
# GLOBAL GENERATOR INSTANCE
# ============================================
# Create single instance for the bot
mcq_generator = GeminiMCQGenerator(GEMINI_KEYS)

log("✅ Gemini MCQ Generator module initialized")
