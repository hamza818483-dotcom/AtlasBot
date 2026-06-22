"""Unit tests for exam_server.py — ATLAS Exam Server utilities."""
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

import exam_server


# ============================================================
# Tests for _parse_new_exam_json — JSON parsing for new exam MCQs
# ============================================================

class TestParseNewExamJson:
    """Tests for _parse_new_exam_json() — parse MCQ JSON from Gemini."""

    def test_parses_valid_json_array(self):
        data = json.dumps([
            {"question": "Q1", "options": ["A", "B", "C", "D"], "answer": 0},
            {"question": "Q2", "options": ["A", "B", "C", "D"], "answer": 2},
        ])
        result = exam_server._parse_new_exam_json(data)
        assert len(result) == 2
        assert result[0]["question"] == "Q1"
        assert result[1]["answer"] == 2

    def test_strips_code_fences(self):
        data = '```json\n[{"question": "Q", "options": ["A","B","C","D"], "answer": 1}]\n```'
        result = exam_server._parse_new_exam_json(data)
        assert len(result) == 1

    def test_strips_plain_backticks(self):
        data = '```\n[{"question": "Q", "options": ["A","B","C","D"], "answer": 0}]\n```'
        result = exam_server._parse_new_exam_json(data)
        assert len(result) == 1

    def test_converts_string_answer_to_int(self):
        data = json.dumps([{"question": "Q", "options": ["A", "B", "C", "D"], "answer": "C"}])
        result = exam_server._parse_new_exam_json(data)
        assert len(result) == 1
        assert result[0]["answer"] == 2

    def test_converts_lowercase_string_answer(self):
        data = json.dumps([{"question": "Q", "options": ["A", "B", "C", "D"], "answer": "d"}])
        result = exam_server._parse_new_exam_json(data)
        assert result[0]["answer"] == 3

    def test_rejects_invalid_answer_out_of_range(self):
        data = json.dumps([{"question": "Q", "options": ["A", "B", "C", "D"], "answer": 5}])
        result = exam_server._parse_new_exam_json(data)
        assert len(result) == 0

    def test_rejects_negative_answer(self):
        data = json.dumps([{"question": "Q", "options": ["A", "B", "C", "D"], "answer": -1}])
        result = exam_server._parse_new_exam_json(data)
        assert len(result) == 0

    def test_allows_mcq_with_fewer_than_4_options_if_answer_valid(self):
        # Unlike bot.parse_mcq_json, _parse_new_exam_json doesn't reject <4 options
        data = json.dumps([{"question": "Q", "options": ["A", "B"], "answer": 0}])
        result = exam_server._parse_new_exam_json(data)
        assert len(result) == 1

    def test_rejects_mcq_with_answer_beyond_options(self):
        # answer=3 but only 2 options — still valid per int range check
        data = json.dumps([{"question": "Q", "options": ["A", "B"], "answer": 4}])
        result = exam_server._parse_new_exam_json(data)
        assert len(result) == 0

    def test_truncates_options_to_4(self):
        data = json.dumps([{"question": "Q", "options": ["A", "B", "C", "D", "E"], "answer": 0}])
        result = exam_server._parse_new_exam_json(data)
        assert len(result) == 1
        assert len(result[0]["options"]) == 4

    def test_returns_empty_for_invalid_json(self):
        result = exam_server._parse_new_exam_json("not json at all")
        assert result == []

    def test_returns_empty_for_empty_string(self):
        result = exam_server._parse_new_exam_json("")
        assert result == []

    def test_returns_empty_for_none(self):
        result = exam_server._parse_new_exam_json(None)
        assert result == []

    def test_rejects_mcq_missing_required_keys(self):
        data = json.dumps([{"question": "Q", "options": ["A", "B", "C", "D"]}])  # no answer
        result = exam_server._parse_new_exam_json(data)
        assert len(result) == 0

    def test_handles_non_list_json(self):
        data = json.dumps({"question": "Q"})
        result = exam_server._parse_new_exam_json(data)
        assert result == []


# ============================================================
# Tests for store_exam / _get_exam — in-memory exam store
# ============================================================

class TestStoreExam:
    """Tests for store_exam() and _get_exam() — in-memory exam storage."""

    def setup_method(self):
        exam_server.exam_store.clear()

    def test_store_and_retrieve(self):
        mcqs = [{"question": "Q", "options": ["A", "B", "C", "D"], "answer": 0}]
        exam_server.store_exam("test123", mcqs, topic="Test Topic", page=1)
        data = exam_server.exam_store.get("test123")
        assert data is not None
        assert data["mcqs"] == mcqs
        assert data["topic"] == "Test Topic"
        assert data["page"] == 1

    def test_store_with_all_params(self):
        mcqs = [{"question": "Q", "options": ["A", "B", "C", "D"], "answer": 0}]
        exam_server.store_exam(
            "full_test", mcqs, topic="Full",
            page=2, tag="bio", image_file_id="img123",
            is_new_gen=True, src_cache_id="src_abc",
            chat_id=100, message_id=200, prompt_type="prompt_2"
        )
        data = exam_server.exam_store["full_test"]
        assert data["tag"] == "bio"
        assert data["image_file_id"] == "img123"
        assert data["is_new_gen"] is True
        assert data["src_cache_id"] == "src_abc"
        assert data["chat_id"] == 100
        assert data["message_id"] == 200
        assert data["prompt_type"] == "prompt_2"
        assert data["regen_count"] == 0

    def test_store_defaults_src_cache_id_to_quiz_id(self):
        mcqs = [{"question": "Q", "options": ["A", "B", "C", "D"], "answer": 0}]
        exam_server.store_exam("default_src", mcqs)
        data = exam_server.exam_store["default_src"]
        assert data["src_cache_id"] == "default_src"

    def test_store_returns_quiz_id(self):
        mcqs = []
        result = exam_server.store_exam("return_id", mcqs)
        assert result == "return_id"

    def test_store_records_created_at(self):
        mcqs = []
        exam_server.store_exam("time_test", mcqs)
        data = exam_server.exam_store["time_test"]
        assert "created_at" in data
        # Should be a valid ISO timestamp
        assert "T" in data["created_at"]


# ============================================================
# Tests for _pick_feedback — score-based motivation
# ============================================================

class TestPickFeedback:
    """Tests for _pick_feedback() — returns motivation message + ayat."""

    def test_excellent_score(self):
        msg, ayat = exam_server._pick_feedback(9, 10)  # 90%
        assert isinstance(msg, str)
        assert isinstance(ayat, str)
        assert "🌙" in ayat

    def test_good_score(self):
        msg, ayat = exam_server._pick_feedback(7, 10)  # 70%
        assert isinstance(msg, str)

    def test_average_score(self):
        msg, ayat = exam_server._pick_feedback(5, 10)  # 50%
        assert isinstance(msg, str)

    def test_poor_score(self):
        msg, ayat = exam_server._pick_feedback(2, 10)  # 20%
        assert isinstance(msg, str)

    def test_zero_total_does_not_crash(self):
        msg, ayat = exam_server._pick_feedback(0, 0)
        assert isinstance(msg, str)
        assert isinstance(ayat, str)

    def test_perfect_score(self):
        msg, ayat = exam_server._pick_feedback(10, 10)  # 100%
        assert isinstance(msg, str)


# ============================================================
# Tests for _esc — HTML escape utility
# ============================================================

class TestEsc:
    """Tests for _esc() — HTML escaping."""

    def test_escapes_ampersand(self):
        assert exam_server._esc("A & B") == "A &amp; B"

    def test_escapes_less_than(self):
        assert exam_server._esc("a < b") == "a &lt; b"

    def test_escapes_greater_than(self):
        assert exam_server._esc("a > b") == "a &gt; b"

    def test_escapes_all_together(self):
        assert exam_server._esc("<script>alert('xss')&</script>") == \
            "&lt;script&gt;alert('xss')&amp;&lt;/script&gt;"

    def test_handles_none(self):
        assert exam_server._esc(None) == ""

    def test_handles_empty_string(self):
        assert exam_server._esc("") == ""

    def test_preserves_normal_text(self):
        assert exam_server._esc("Hello World") == "Hello World"


# ============================================================
# Tests for _not_found_html — 404 page
# ============================================================

class TestNotFoundHtml:
    """Tests for _not_found_html() — generates 404 HTML."""

    def test_returns_html_string(self):
        result = exam_server._not_found_html()
        assert "<!DOCTYPE html>" in result
        assert "</html>" in result

    def test_contains_error_message(self):
        result = exam_server._not_found_html()
        assert "পাওয়া যায়নি" in result


# ============================================================
# Tests for _b64_data_url (exam_server version)
# ============================================================

class TestExamB64DataUrl:
    """Tests for exam_server._b64_data_url() — base64 data URL."""

    def test_jpeg_detection(self):
        jpeg_bytes = b'\xff\xd8\xff\xe0' + b'\x00' * 100
        result = exam_server._b64_data_url(jpeg_bytes)
        assert result.startswith("data:image/jpeg;base64,")

    def test_png_detection(self):
        png_bytes = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        result = exam_server._b64_data_url(png_bytes)
        assert result.startswith("data:image/png;base64,")

    def test_webp_detection(self):
        webp_bytes = b'RIFF\x00\x00\x00\x00WEBP' + b'\x00' * 100
        result = exam_server._b64_data_url(webp_bytes)
        assert result.startswith("data:image/webp;base64,")

    def test_default_is_jpeg(self):
        unknown_bytes = b'\x00\x01\x02\x03' + b'\x00' * 100
        result = exam_server._b64_data_url(unknown_bytes)
        assert result.startswith("data:image/jpeg;base64,")


# ============================================================
# Tests for check_new_exam_limit (with mocked DB)
# ============================================================

class TestCheckNewExamLimit:
    """Tests for check_new_exam_limit() — rate limiting."""

    def test_returns_allowed_when_no_user(self):
        allowed, used, limit, is_perm = exam_server.check_new_exam_limit(0)
        assert allowed is True
        assert used == 0

    @patch.object(exam_server, "get_user_data")
    def test_returns_allowed_when_user_not_found(self, mock_get):
        mock_get.return_value = None
        allowed, used, limit, is_perm = exam_server.check_new_exam_limit(123)
        assert allowed is True
        assert is_perm is False

    @patch.object(exam_server, "get_user_data")
    @patch.object(exam_server, "get_supabase")
    def test_permitted_user_gets_higher_limit(self, mock_sb, mock_get):
        mock_get.return_value = {
            "is_permitted": True,
            "new_exam_count": 5,
            "last_new_exam_reset": datetime.now(exam_server.BD_TZ).strftime('%Y-%m-%d')
        }
        allowed, used, limit, is_perm = exam_server.check_new_exam_limit(456)
        assert is_perm is True
        assert limit == exam_server.PERMITTED_NEW_EXAM_LIMIT

    @patch.object(exam_server, "get_user_data")
    @patch.object(exam_server, "get_supabase")
    def test_free_user_gets_lower_limit(self, mock_sb, mock_get):
        mock_get.return_value = {
            "is_permitted": False,
            "new_exam_count": 1,
            "last_new_exam_reset": datetime.now(exam_server.BD_TZ).strftime('%Y-%m-%d')
        }
        allowed, used, limit, is_perm = exam_server.check_new_exam_limit(789)
        assert is_perm is False
        assert limit == exam_server.FREE_NEW_EXAM_LIMIT
        assert allowed is True

    @patch.object(exam_server, "get_user_data")
    @patch.object(exam_server, "get_supabase")
    def test_not_allowed_when_limit_reached(self, mock_sb, mock_get):
        mock_get.return_value = {
            "is_permitted": False,
            "new_exam_count": exam_server.FREE_NEW_EXAM_LIMIT,
            "last_new_exam_reset": datetime.now(exam_server.BD_TZ).strftime('%Y-%m-%d')
        }
        allowed, used, limit, is_perm = exam_server.check_new_exam_limit(999)
        assert allowed is False


# ============================================================
# Tests for increment_new_exam_count (with mocked DB)
# ============================================================

class TestIncrementNewExamCount:
    """Tests for increment_new_exam_count() — increments usage counter."""

    def test_returns_zero_for_user_id_zero(self):
        result = exam_server.increment_new_exam_count(0)
        assert result == 0

    @patch.object(exam_server, "get_user_data")
    def test_returns_zero_when_user_not_found(self, mock_get):
        mock_get.return_value = None
        result = exam_server.increment_new_exam_count(123)
        assert result == 0

    @patch.object(exam_server, "get_user_data")
    @patch.object(exam_server, "get_supabase")
    def test_increments_count(self, mock_sb, mock_get):
        mock_get.return_value = {"new_exam_count": 3}
        mock_client = MagicMock()
        mock_sb.return_value = mock_client
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

        result = exam_server.increment_new_exam_count(456)
        assert result == 4


# ============================================================
# Tests for save_result_to_db (with mocked DB)
# ============================================================

class TestSaveResultToDb:
    """Tests for save_result_to_db() — saves exam results."""

    @patch.object(exam_server, "_mirror_insert")
    @patch.object(exam_server, "get_supabase")
    def test_saves_result_successfully(self, mock_sb, mock_mirror):
        mock_client = MagicMock()
        mock_sb.return_value = mock_client
        mock_client.table.return_value.insert.return_value.execute.return_value = MagicMock()

        result = exam_server.save_result_to_db(
            user_id=123, cache_id="abc", user_name="Test",
            topic="Bio", page=1, total=10, correct=7,
            wrong=2, skipped=1, time_taken=120
        )
        assert result is True
        mock_client.table.assert_called_with("results")

    @patch.object(exam_server, "get_supabase")
    def test_handles_db_error(self, mock_sb):
        mock_sb.side_effect = Exception("DB error")
        result = exam_server.save_result_to_db(
            user_id=123, cache_id="abc", user_name="Test",
            topic="Bio", page=1, total=10, correct=7,
            wrong=2, skipped=1, time_taken=120
        )
        assert result is False

    @patch.object(exam_server, "_mirror_insert")
    @patch.object(exam_server, "get_supabase")
    def test_calculates_negative_mark(self, mock_sb, mock_mirror):
        mock_client = MagicMock()
        mock_sb.return_value = mock_client
        mock_client.table.return_value.insert.return_value.execute.return_value = MagicMock()

        exam_server.save_result_to_db(
            user_id=1, cache_id="x", user_name="U",
            topic="T", page=1, total=5, correct=3,
            wrong=2, skipped=0, time_taken=60
        )
        # Verify the inserted row contains correct calculation
        call_args = mock_client.table.return_value.insert.call_args[0][0]
        assert call_args["negative_mark"] == 2 * exam_server.NEGATIVE_MARK
        assert call_args["mark"] == 3 - (2 * exam_server.NEGATIVE_MARK)


# ============================================================
# Tests for save_bookmark_to_db / delete_bookmark_from_db
# ============================================================

class TestBookmarkDb:
    """Tests for bookmark DB operations."""

    @patch.object(exam_server, "_mirror_insert")
    @patch.object(exam_server, "get_supabase")
    def test_save_bookmark_success(self, mock_sb, mock_mirror):
        mock_client = MagicMock()
        mock_sb.return_value = mock_client
        mock_client.table.return_value.insert.return_value.execute.return_value = MagicMock()

        ok, err = exam_server.save_bookmark_to_db(
            user_id=123, cache_id="abc", question_index=0,
            question_data={"question": "Q?"}, topic="Test", page=1
        )
        assert ok is True
        assert err == ""

    @patch.object(exam_server, "get_supabase")
    def test_save_bookmark_rls_blocked(self, mock_sb):
        mock_client = MagicMock()
        mock_sb.return_value = mock_client
        mock_client.table.return_value.insert.return_value.execute.side_effect = \
            Exception("42501 row-level security policy violation")

        ok, err = exam_server.save_bookmark_to_db(
            user_id=123, cache_id="abc", question_index=0,
            question_data={}, topic="", page=0
        )
        assert ok is False
        assert err == "RLS_BLOCKED"

    @patch.object(exam_server, "get_supabase")
    def test_delete_bookmark_success(self, mock_sb):
        mock_client = MagicMock()
        mock_sb.return_value = mock_client
        mock_client.table.return_value.delete.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock()

        result = exam_server.delete_bookmark_from_db(123, "abc", 0)
        assert result is True

    @patch.object(exam_server, "get_supabase")
    def test_delete_bookmark_failure(self, mock_sb):
        mock_sb.side_effect = Exception("error")
        result = exam_server.delete_bookmark_from_db(123, "abc", 0)
        assert result is False
