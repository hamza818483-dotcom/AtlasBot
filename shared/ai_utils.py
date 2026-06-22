"""
Shared AI/JSON utilities used by both bot.py and exam_server.py.

Consolidates duplicated MCQ JSON parsing, fixing, base64 encoding,
and option-prefix cleaning into one module.
"""

import re
import json
import base64
from typing import List, Dict


def b64_data_url(image_bytes: bytes) -> str:
    """Convert raw image bytes to a base64 data-URL with correct MIME type."""
    mime = "image/jpeg"
    if image_bytes[:8].startswith(b"\x89PNG"):
        mime = "image/png"
    elif image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        mime = "image/webp"
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"


def strip_json_markdown(text: str) -> str:
    """Remove ```json / ``` markdown fencing from AI responses."""
    t = (text or "").strip()
    for tag in ['```json', '```']:
        if t.startswith(tag):
            t = t[len(tag):]
    if t.endswith('```'):
        t = t[:-3]
    return t.strip()


def fix_json_str(t: str) -> str:
    """Fix common AI JSON issues: trailing commas, missing values, truncation."""
    t = re.sub(r',\s*([}\]])', r'\1', t)
    t = re.sub(r':\s*,', ': "",', t)
    t = re.sub(r':\s*}', ': ""}', t)
    t = re.sub(r',\s*$', ']', t)
    if t.count('[') > t.count(']'):
        t = t.rstrip().rstrip(',') + ']'
    if t.count('{') > t.count('}'):
        t = t.rstrip().rstrip(',') + '}'
        if t.count('[') > t.count(']'):
            t = t + ']'
    return t


def extract_mcq_objects(t: str) -> List[Dict]:
    """Extract individual MCQ JSON objects from messy text using brace matching."""
    mcqs = []
    i = 0
    while i < len(t):
        if t[i] == '{':
            depth = 0
            start = i
            for j in range(i, len(t)):
                if t[j] == '{':
                    depth += 1
                elif t[j] == '}':
                    depth -= 1
                if depth == 0:
                    candidate = t[start:j+1]
                    if '"question"' in candidate and '"options"' in candidate and '"answer"' in candidate:
                        try:
                            obj = json.loads(candidate)
                            mcqs.append(obj)
                        except json.JSONDecodeError:
                            try:
                                mcqs.append(json.loads(fix_json_str(candidate)))
                            except json.JSONDecodeError:
                                pass
                    i = j + 1
                    break
            else:
                break
        else:
            i += 1
    return mcqs


_OPT_PREFIX_RE = re.compile(r'^\s*[\(\[]?\s*([A-Da-d]|[কখগঘ])\s*[\)\.\:\]।]\s*')


def clean_option_prefix(opt: str, idx: int = 0) -> str:
    """Remove A)/a)/ক) etc. prefix from quiz option text."""
    if not isinstance(opt, str):
        return opt
    cleaned = _OPT_PREFIX_RE.sub('', opt, count=1).strip()
    return cleaned if cleaned else opt


def parse_mcq_json(response_text: str, clean_prefixes: bool = True,
                   error_logger=None) -> List[Dict]:
    """Shared cleaner+parser+validator for MCQ JSON from any AI provider.

    Args:
        response_text: Raw AI response text.
        clean_prefixes: Whether to strip A)/B) prefixes from options.
        error_logger: Optional callable(msg) for logging parse failures.
    """
    t = strip_json_markdown(response_text)
    if not t.startswith('['):
        s, e = t.find('['), t.rfind(']')
        if s != -1 and e != -1 and e > s:
            t = t[s:e+1]
    mcqs = None
    try:
        mcqs = json.loads(t)
    except json.JSONDecodeError as je:
        if "Extra data" in str(je) and je.pos > 0:
            try:
                mcqs = json.loads(t[:je.pos])
            except json.JSONDecodeError:
                pass
    if mcqs is None:
        try:
            mcqs = json.loads(fix_json_str(t))
        except json.JSONDecodeError:
            pass
    if mcqs is None:
        mcqs = extract_mcq_objects(t)
    if not mcqs:
        if error_logger:
            error_logger(
                f"parse_mcq_json: all strategies failed, input len={len(t)}, "
                f"first 300 chars: {t[:300]}"
            )
        return []
    valid = []
    for mcq in mcqs:
        if all(k in mcq for k in ['question', 'options', 'answer']):
            if len(mcq['options']) >= 4:
                mcq['options'] = mcq['options'][:4]
            if isinstance(mcq['answer'], str):
                mcq['answer'] = {'A': 0, 'B': 1, 'C': 2, 'D': 3}.get(
                    mcq['answer'].upper(), 0
                )
            if isinstance(mcq['answer'], int) and 0 <= mcq['answer'] <= 3 and len(mcq['options']) == 4:
                if clean_prefixes:
                    mcq['options'] = [
                        clean_option_prefix(o, i) for i, o in enumerate(mcq['options'])
                    ]
                valid.append(mcq)
    return valid
