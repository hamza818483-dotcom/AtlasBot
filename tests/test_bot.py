"""Unit tests for bot.py — ATLAS MCQ Bot utility/parsing functions."""
import os
import sys
import json
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta, timezone

# Ensure env is set before import
os.environ.setdefault("BOT_TOKEN", "fake-token")
os.environ.setdefault("OWNER_ID", "12345")
os.environ.setdefault("GEMINI_KEY", "")

import bot


# ============================================================
# Tests for _fix_json_str — JSON repair utility
# ============================================================

class TestFixJsonStr:
    """Tests for _fix_json_str() — fixes common AI JSON issues."""

    def test_removes_trailing_comma_before_bracket(self):
        result = bot._fix_json_str('[{"a": 1},]')
        assert result == '[{"a": 1}]'

    def test_removes_trailing_comma_before_brace(self):
        result = bot._fix_json_str('{"a": 1, "b": 2,}')
        assert result == '{"a": 1, "b": 2}'

    def test_fixes_missing_value_before_comma(self):
        result = bot._fix_json_str('{"key": ,}')
        assert '"key": ""' in result

    def test_fixes_missing_value_before_brace(self):
        result = bot._fix_json_str('{"key": }')
        assert '"key": ""' in result

    def test_closes_unclosed_array(self):
        result = bot._fix_json_str('[{"a": 1}, {"b": 2}')
        assert result.endswith(']')

    def test_closes_unclosed_object_and_array(self):
        result = bot._fix_json_str('[{"a": 1')
        assert '}' in result
        assert ']' in result

    def test_passthrough_valid_json(self):
        valid = '[{"question": "Q1", "answer": 0}]'
        result = bot._fix_json_str(valid)
        # Should be parseable
        json.loads(result)

    def test_handles_empty_string(self):
        result = bot._fix_json_str('')
        assert result == ''


# ============================================================
# Tests for _extract_mcq_objects — brace-matching extraction
# ============================================================

class TestExtractMcqObjects:
    """Tests for _extract_mcq_objects() — extracts MCQ JSON from messy text."""

    def test_extracts_valid_mcq_objects(self):
        text = '[{"question": "What?", "options": ["A", "B", "C", "D"], "answer": 0}]'
        result = bot._extract_mcq_objects(text)
        assert len(result) == 1
        assert result[0]["question"] == "What?"

    def test_extracts_multiple_mcq_objects(self):
        text = (
            'Some text {"question": "Q1", "options": ["A","B","C","D"], "answer": 1} '
            'more text {"question": "Q2", "options": ["A","B","C","D"], "answer": 2}'
        )
        result = bot._extract_mcq_objects(text)
        assert len(result) == 2

    def test_skips_non_mcq_objects(self):
        text = '{"foo": "bar"} {"question": "Q", "options": ["A","B","C","D"], "answer": 0}'
        result = bot._extract_mcq_objects(text)
        assert len(result) == 1
        assert result[0]["question"] == "Q"

    def test_returns_empty_for_no_matches(self):
        result = bot._extract_mcq_objects("no json here at all")
        assert result == []

    def test_handles_nested_braces(self):
        text = '{"question": "What is {x}?", "options": ["A","B","C","D"], "answer": 0}'
        result = bot._extract_mcq_objects(text)
        assert len(result) == 1

    def test_handles_empty_string(self):
        result = bot._extract_mcq_objects("")
        assert result == []


# ============================================================
# Tests for parse_mcq_json — full MCQ parsing pipeline
# ============================================================

class TestParseMcqJson:
    """Tests for parse_mcq_json() — the main MCQ JSON parser."""

    def test_parses_valid_json_array(self):
        data = json.dumps([
            {"question": "Q1", "options": ["A", "B", "C", "D"], "answer": 0, "explanation": "E1"},
            {"question": "Q2", "options": ["A", "B", "C", "D"], "answer": 2, "explanation": "E2"},
        ])
        result = bot.parse_mcq_json(data)
        assert len(result) == 2
        assert result[0]["question"] == "Q1"
        assert result[1]["answer"] == 2

    def test_strips_code_fences(self):
        data = '```json\n[{"question": "Q", "options": ["A","B","C","D"], "answer": 1}]\n```'
        result = bot.parse_mcq_json(data)
        assert len(result) == 1

    def test_strips_triple_backtick_only(self):
        data = '```\n[{"question": "Q", "options": ["A","B","C","D"], "answer": 0}]\n```'
        result = bot.parse_mcq_json(data)
        assert len(result) == 1

    def test_extracts_json_from_surrounding_text(self):
        data = 'Here are the MCQs:\n[{"question": "Q", "options": ["A","B","C","D"], "answer": 0}]\nDone!'
        result = bot.parse_mcq_json(data)
        assert len(result) == 1

    def test_converts_string_answer_to_int(self):
        data = json.dumps([{"question": "Q", "options": ["A", "B", "C", "D"], "answer": "B"}])
        result = bot.parse_mcq_json(data)
        assert len(result) == 1
        assert result[0]["answer"] == 1

    def test_converts_lowercase_answer(self):
        data = json.dumps([{"question": "Q", "options": ["A", "B", "C", "D"], "answer": "c"}])
        result = bot.parse_mcq_json(data)
        assert result[0]["answer"] == 2

    def test_rejects_invalid_answer_index(self):
        data = json.dumps([{"question": "Q", "options": ["A", "B", "C", "D"], "answer": 5}])
        result = bot.parse_mcq_json(data)
        assert len(result) == 0

    def test_rejects_mcq_with_fewer_than_4_options(self):
        data = json.dumps([{"question": "Q", "options": ["A", "B"], "answer": 0}])
        result = bot.parse_mcq_json(data)
        assert len(result) == 0

    def test_truncates_options_to_4(self):
        data = json.dumps([{"question": "Q", "options": ["A", "B", "C", "D", "E", "F"], "answer": 0}])
        result = bot.parse_mcq_json(data)
        assert len(result) == 1
        assert len(result[0]["options"]) == 4

    def test_returns_empty_for_garbage(self):
        result = bot.parse_mcq_json("totally not json at all!!! 12345")
        assert result == []

    def test_returns_empty_for_none(self):
        result = bot.parse_mcq_json(None)
        assert result == []

    def test_returns_empty_for_empty_string(self):
        result = bot.parse_mcq_json("")
        assert result == []

    def test_handles_extra_data_in_json(self):
        data = '[{"question": "Q1", "options": ["A","B","C","D"], "answer": 0}][{"extra": true}]'
        result = bot.parse_mcq_json(data)
        assert len(result) >= 1


# ============================================================
# Tests for _b64_data_url — image bytes to data URL
# ============================================================

class TestB64DataUrl:
    """Tests for _b64_data_url() — base64 data URL generation."""

    def test_jpeg_detection(self):
        # JPEG magic bytes: FF D8 FF
        jpeg_bytes = b'\xff\xd8\xff\xe0' + b'\x00' * 100
        result = bot._b64_data_url(jpeg_bytes)
        assert result.startswith("data:image/jpeg;base64,")

    def test_png_detection(self):
        # PNG magic bytes: 89 50 4E 47 0D 0A 1A 0A
        png_bytes = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        result = bot._b64_data_url(png_bytes)
        assert result.startswith("data:image/png;base64,")

    def test_webp_detection(self):
        # WebP magic: RIFF....WEBP
        webp_bytes = b'RIFF\x00\x00\x00\x00WEBP' + b'\x00' * 100
        result = bot._b64_data_url(webp_bytes)
        assert result.startswith("data:image/webp;base64,")

    def test_default_is_jpeg(self):
        # Unknown format defaults to jpeg
        unknown_bytes = b'\x00\x01\x02\x03' + b'\x00' * 100
        result = bot._b64_data_url(unknown_bytes)
        assert result.startswith("data:image/jpeg;base64,")

    def test_base64_encoding_roundtrip(self):
        import base64
        test_bytes = b'test image content here'
        result = bot._b64_data_url(test_bytes)
        encoded_part = result.split(",", 1)[1]
        decoded = base64.b64decode(encoded_part)
        assert decoded == test_bytes


# ============================================================
# Tests for _key_prefix — safe key fingerprint
# ============================================================

class TestKeyPrefix:
    """Tests for _key_prefix() — key fingerprint display."""

    def test_empty_string(self):
        assert bot._key_prefix("") == "—"

    def test_short_key(self):
        result = bot._key_prefix("abcdef")
        assert result == "abcd…"

    def test_long_key(self):
        result = bot._key_prefix("abcdefghijklmnop")
        assert result == "abcdef…mnop"

    def test_exactly_12_chars(self):
        result = bot._key_prefix("123456789012")
        assert result == "1234…"

    def test_13_chars(self):
        result = bot._key_prefix("1234567890123")
        assert result == "123456…0123"


# ============================================================
# Tests for clean_option_prefix — removes A)/B)/ক) prefixes
# ============================================================

class TestCleanOptionPrefix:
    """Tests for clean_option_prefix() — strips option letter prefixes."""

    def test_removes_A_paren(self):
        assert bot.clean_option_prefix("A) Option text") == "Option text"

    def test_removes_B_paren(self):
        assert bot.clean_option_prefix("B) Second option") == "Second option"

    def test_removes_lowercase_a_paren(self):
        assert bot.clean_option_prefix("a) lower case") == "lower case"

    def test_removes_bangla_prefix(self):
        assert bot.clean_option_prefix("ক) বাংলা অপশন") == "বাংলা অপশন"

    def test_removes_bracket_prefix(self):
        assert bot.clean_option_prefix("[A] Bracket style") == "Bracket style"

    def test_removes_dot_prefix(self):
        assert bot.clean_option_prefix("C. Dot style") == "Dot style"

    def test_preserves_text_without_prefix(self):
        assert bot.clean_option_prefix("No prefix here") == "No prefix here"

    def test_preserves_non_string(self):
        assert bot.clean_option_prefix(123) == 123

    def test_returns_original_if_cleaning_makes_empty(self):
        # If the entire string is just the prefix pattern
        result = bot.clean_option_prefix("A)")
        # After regex sub, the result should be empty, so original is returned
        assert result == "A)"


# ============================================================
# Tests for _track_attempt — provider stats tracking
# ============================================================

class TestTrackAttempt:
    """Tests for _track_attempt() — tracks AI provider usage stats."""

    def setup_method(self):
        bot._provider_stats = {}
        bot._provider_stats_day = datetime.now(bot.BD_TZ).strftime('%Y-%m-%d')

    def test_tracks_success(self):
        bot._track_attempt("gemini", "gemini#1", ok=True)
        assert bot._provider_stats["gemini"]["gemini#1"]["ok"] == 1
        assert bot._provider_stats["gemini"]["gemini#1"]["fail"] == 0

    def test_tracks_failure(self):
        bot._track_attempt("nvidia", "nvidia#1", ok=False)
        assert bot._provider_stats["nvidia"]["nvidia#1"]["fail"] == 1

    def test_tracks_exhausted(self):
        bot._track_attempt("groq", "groq#1", ok=False, exhausted=True)
        assert bot._provider_stats["groq"]["groq#1"]["exhausted"] is True

    def test_increments_counters(self):
        bot._track_attempt("gemini", "gemini#1", ok=True)
        bot._track_attempt("gemini", "gemini#1", ok=True)
        bot._track_attempt("gemini", "gemini#1", ok=False)
        assert bot._provider_stats["gemini"]["gemini#1"]["ok"] == 2
        assert bot._provider_stats["gemini"]["gemini#1"]["fail"] == 1

    def test_skips_empty_provider(self):
        bot._track_attempt("", "label", ok=True)
        assert "" not in bot._provider_stats

    def test_records_last_time(self):
        bot._track_attempt("test", "test#1", ok=True)
        assert bot._provider_stats["test"]["test#1"]["last"] != ""


# ============================================================
# Tests for _reset_provider_stats_if_new_day
# ============================================================

class TestResetProviderStats:
    """Tests for _reset_provider_stats_if_new_day()."""

    def test_resets_on_new_day(self):
        bot._provider_stats = {"gemini": {"gemini#1": {"ok": 5}}}
        bot._provider_stats_day = "2020-01-01"  # force old day
        bot._reset_provider_stats_if_new_day()
        assert bot._provider_stats == {}

    def test_does_not_reset_on_same_day(self):
        today = datetime.now(bot.BD_TZ).strftime('%Y-%m-%d')
        bot._provider_stats = {"gemini": {"gemini#1": {"ok": 5}}}
        bot._provider_stats_day = today
        bot._reset_provider_stats_if_new_day()
        assert bot._provider_stats == {"gemini": {"gemini#1": {"ok": 5}}}


# ============================================================
# Tests for get_feedback — percentage-based feedback
# ============================================================

class TestGetFeedback:
    """Tests for get_feedback() — returns feedback string based on score percentage."""

    def test_excellent_range(self):
        result = bot.get_feedback(95)
        assert result in bot.FEEDBACKS['excellent']

    def test_good_range(self):
        result = bot.get_feedback(80)
        assert result in bot.FEEDBACKS['good']

    def test_average_range(self):
        result = bot.get_feedback(60)
        assert result in bot.FEEDBACKS['average']

    def test_poor_range(self):
        result = bot.get_feedback(30)
        assert result in bot.FEEDBACKS['poor']

    def test_boundary_90(self):
        result = bot.get_feedback(90)
        assert result in bot.FEEDBACKS['excellent']

    def test_boundary_75(self):
        result = bot.get_feedback(75)
        assert result in bot.FEEDBACKS['good']

    def test_boundary_50(self):
        result = bot.get_feedback(50)
        assert result in bot.FEEDBACKS['average']


# ============================================================
# Tests for get_ayat — score-based Quranic verse selection
# ============================================================

class TestGetAyat:
    """Tests for get_ayat() — returns Quranic verse based on score."""

    def test_high_score_categories(self):
        result = bot.get_ayat(90)
        all_ayats = bot.AYATS['success'] + bot.AYATS['hope']
        assert result in all_ayats

    def test_medium_score_categories(self):
        result = bot.get_ayat(65)
        all_ayats = bot.AYATS['hope'] + bot.AYATS['effort']
        assert result in all_ayats

    def test_low_score_categories(self):
        result = bot.get_ayat(45)
        all_ayats = bot.AYATS['effort'] + bot.AYATS['patience']
        assert result in all_ayats

    def test_very_low_score_categories(self):
        result = bot.get_ayat(20)
        all_ayats = bot.AYATS['hardship'] + bot.AYATS['patience']
        assert result in all_ayats

    def test_none_score_categories(self):
        result = bot.get_ayat(None)
        all_ayats = bot.AYATS['tawakkul'] + bot.AYATS['exam'] + bot.AYATS['ibadah']
        assert result in all_ayats


# ============================================================
# Tests for format_poll_question
# ============================================================

class TestFormatPollQuestion:
    """Tests for format_poll_question() — formats question text for polls."""

    def test_basic_formatting(self):
        mcq = {"question": "What is X?"}
        result = bot.format_poll_question(mcq, 1)
        assert result == "1. What is X?"

    def test_with_tag(self):
        mcq = {"question": "What is X?", "_tag": "Biology"}
        result = bot.format_poll_question(mcq, 3)
        assert result == "[Biology]\n\n3. What is X?"

    def test_truncates_to_300_chars(self):
        mcq = {"question": "A" * 400}
        result = bot.format_poll_question(mcq, 1)
        assert len(result) <= 300

    def test_empty_tag_not_shown(self):
        mcq = {"question": "Q?", "_tag": ""}
        result = bot.format_poll_question(mcq, 1)
        assert "[" not in result


# ============================================================
# Tests for format_explanation
# ============================================================

class TestFormatExplanation:
    """Tests for format_explanation() — formats explanation text."""

    def test_basic_explanation(self):
        mcq = {"explanation": "This is the explanation."}
        result = bot.format_explanation(mcq)
        assert result == "This is the explanation."

    def test_with_suffix(self):
        mcq = {"explanation": "Explain", "_exp": "Chapter 5"}
        result = bot.format_explanation(mcq)
        assert result == "Explain\n\n📌 Chapter 5"

    def test_truncates_to_200_chars(self):
        mcq = {"explanation": "A" * 300}
        result = bot.format_explanation(mcq)
        assert len(result) <= 200

    def test_missing_explanation(self):
        mcq = {}
        result = bot.format_explanation(mcq)
        assert "পাওয়া যায়নি" in result


# ============================================================
# Tests for _progress_bar
# ============================================================

class TestProgressBar:
    """Tests for _progress_bar() — visual progress bar."""

    def test_zero_percent(self):
        result = bot._progress_bar(0)
        assert result == "▱" * 7

    def test_hundred_percent(self):
        result = bot._progress_bar(100)
        assert result == "▰" * 7

    def test_fifty_percent(self):
        result = bot._progress_bar(50)
        # 50% of 7 = 3.5, rounded = 4
        assert result.count("▰") == 4
        assert result.count("▱") == 3

    def test_length_always_7(self):
        for pct in [0, 10, 25, 50, 75, 100]:
            result = bot._progress_bar(pct)
            assert len(result) == 7


# ============================================================
# Tests for clean_mcq_options
# ============================================================

class TestCleanMcqOptions:
    """Tests for clean_mcq_options() — strips prefixes from all MCQ options."""

    def test_cleans_all_options(self):
        mcqs = [{"question": "Q?", "options": ["A) Opt1", "B) Opt2", "C) Opt3", "D) Opt4"], "answer": 0}]
        result = bot.clean_mcq_options(mcqs)
        assert result[0]["options"] == ["Opt1", "Opt2", "Opt3", "Opt4"]

    def test_preserves_other_fields(self):
        mcqs = [{"question": "Q?", "options": ["A) X", "B) Y", "C) Z", "D) W"], "answer": 2, "explanation": "E"}]
        result = bot.clean_mcq_options(mcqs)
        assert result[0]["question"] == "Q?"
        assert result[0]["answer"] == 2
        assert result[0]["explanation"] == "E"

    def test_handles_empty_list(self):
        assert bot.clean_mcq_options([]) == []

    def test_does_not_mutate_original(self):
        original = [{"question": "Q", "options": ["A) X", "B) Y", "C) Z", "D) W"]}]
        bot.clean_mcq_options(original)
        assert original[0]["options"][0] == "A) X"


# ============================================================
# Tests for is_admin
# ============================================================

class TestIsAdmin:
    """Tests for is_admin() — checks if user is the bot owner."""

    def test_owner_is_admin(self):
        assert bot.is_admin(bot.OWNER_ID) is True

    def test_non_owner_is_not_admin(self):
        assert bot.is_admin(99999) is False

    def test_zero_is_not_admin(self):
        assert bot.is_admin(0) is False


# ============================================================
# Tests for rotate_gemini_key
# ============================================================

class TestRotateGeminiKey:
    """Tests for rotate_gemini_key() — key rotation."""

    def test_no_rotation_with_single_key(self):
        with patch.object(bot, "GEMINI_KEYS", ["key1"]):
            result = bot.rotate_gemini_key()
            assert result is False

    def test_no_rotation_with_empty_keys(self):
        with patch.object(bot, "GEMINI_KEYS", []):
            result = bot.rotate_gemini_key()
            assert result is False

    def test_rotates_with_multiple_keys(self):
        with patch.object(bot, "GEMINI_KEYS", ["key1", "key2", "key3"]):
            bot._current_key_idx = 0
            result = bot.rotate_gemini_key()
            assert result is True
            assert bot._current_key_idx == 1
