"""Unit tests for storage.py — ATLAS Dual Storage Layer."""
import os
import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

# Ensure env is set before import
os.environ.setdefault("CF_D1_URL", "")
os.environ.setdefault("CF_D1_TOKEN", "")

import storage


class TestD1Enabled:
    """Tests for d1_enabled() — checks if D1 is configured."""

    def test_d1_disabled_when_no_url(self):
        with patch.object(storage, "CF_D1_URL", ""):
            with patch.object(storage, "CF_D1_TOKEN", "some-token"):
                assert storage.d1_enabled() is False

    def test_d1_disabled_when_no_token(self):
        with patch.object(storage, "CF_D1_URL", "https://worker.example.com"):
            with patch.object(storage, "CF_D1_TOKEN", ""):
                assert storage.d1_enabled() is False

    def test_d1_disabled_when_both_empty(self):
        with patch.object(storage, "CF_D1_URL", ""):
            with patch.object(storage, "CF_D1_TOKEN", ""):
                assert storage.d1_enabled() is False

    def test_d1_enabled_when_both_set(self):
        with patch.object(storage, "CF_D1_URL", "https://worker.example.com"):
            with patch.object(storage, "CF_D1_TOKEN", "secret-token"):
                assert storage.d1_enabled() is True


class TestBindSupabase:
    """Tests for bind_supabase() and _sb() helper."""

    def test_bind_supabase_sets_getter(self):
        mock_getter = MagicMock(return_value="fake_client")
        storage.bind_supabase(mock_getter)
        assert storage._supabase_getter is mock_getter

    def test_sb_returns_client_from_getter(self):
        mock_client = MagicMock()
        storage.bind_supabase(lambda: mock_client)
        assert storage._sb() is mock_client

    def test_sb_returns_none_when_no_getter(self):
        storage._supabase_getter = None
        assert storage._sb() is None


class TestSbCount:
    """Tests for sb_count() — Supabase row count."""

    def test_sb_count_returns_zero_when_no_client(self):
        storage._supabase_getter = None
        assert storage.sb_count("mcqs") == 0

    def test_sb_count_returns_count_from_client(self):
        mock_result = MagicMock()
        mock_result.count = 42
        mock_table = MagicMock()
        mock_table.select.return_value.limit.return_value.execute.return_value = mock_result
        mock_client = MagicMock()
        mock_client.table.return_value = mock_table
        storage.bind_supabase(lambda: mock_client)
        assert storage.sb_count("mcqs") == 42
        mock_client.table.assert_called_with("mcqs")

    def test_sb_count_returns_zero_on_exception(self):
        mock_client = MagicMock()
        mock_client.table.side_effect = Exception("DB error")
        storage.bind_supabase(lambda: mock_client)
        assert storage.sb_count("mcqs") == 0


class TestD1Query:
    """Tests for d1_query() — async D1 transport."""

    @pytest.mark.asyncio
    async def test_d1_query_returns_empty_when_disabled(self):
        with patch.object(storage, "CF_D1_URL", ""):
            result = await storage.d1_query("SELECT 1")
            assert result == {}

    @pytest.mark.asyncio
    async def test_d1_query_success(self):
        with patch.object(storage, "CF_D1_URL", "https://worker.test"):
            with patch.object(storage, "CF_D1_TOKEN", "tok"):
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {"ok": True, "results": [{"n": 5}]}

                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client.post = AsyncMock(return_value=mock_response)

                with patch("httpx.AsyncClient", return_value=mock_client):
                    result = await storage.d1_query("SELECT COUNT(*) AS n FROM mcqs")
                    assert result == {"ok": True, "results": [{"n": 5}]}

    @pytest.mark.asyncio
    async def test_d1_query_returns_empty_on_non_200(self):
        with patch.object(storage, "CF_D1_URL", "https://worker.test"):
            with patch.object(storage, "CF_D1_TOKEN", "tok"):
                mock_response = MagicMock()
                mock_response.status_code = 500

                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client.post = AsyncMock(return_value=mock_response)

                with patch("httpx.AsyncClient", return_value=mock_client):
                    result = await storage.d1_query("BAD SQL")
                    assert result == {}

    @pytest.mark.asyncio
    async def test_d1_query_returns_empty_on_exception(self):
        with patch.object(storage, "CF_D1_URL", "https://worker.test"):
            with patch.object(storage, "CF_D1_TOKEN", "tok"):
                with patch("httpx.AsyncClient", side_effect=Exception("network error")):
                    result = await storage.d1_query("SELECT 1")
                    assert result == {}


class TestD1Count:
    """Tests for d1_count() — row count via D1."""

    @pytest.mark.asyncio
    async def test_d1_count_extracts_count(self):
        with patch.object(storage, "d1_query", new_callable=AsyncMock) as mock_q:
            mock_q.return_value = {"results": [{"n": 123}]}
            count = await storage.d1_count("mcqs")
            assert count == 123

    @pytest.mark.asyncio
    async def test_d1_count_returns_zero_on_empty_results(self):
        with patch.object(storage, "d1_query", new_callable=AsyncMock) as mock_q:
            mock_q.return_value = {}
            count = await storage.d1_count("mcqs")
            assert count == 0

    @pytest.mark.asyncio
    async def test_d1_count_returns_zero_on_malformed_response(self):
        with patch.object(storage, "d1_query", new_callable=AsyncMock) as mock_q:
            mock_q.return_value = {"results": [{}]}
            count = await storage.d1_count("mcqs")
            assert count == 0


class TestDualInsert:
    """Tests for dual_insert() — write to primary + mirror."""

    @pytest.mark.asyncio
    async def test_dual_insert_d1_primary_mode(self):
        with patch.object(storage, "STORAGE_MODE", "d1_primary"):
            with patch.object(storage, "d1_query", new_callable=AsyncMock) as mock_d1:
                mock_d1.return_value = {"ok": True}
                storage._supabase_getter = None  # no supabase fallback

                result = await storage.dual_insert("mcqs", {"quiz_id": "abc", "user_id": 1})
                assert result is True
                mock_d1.assert_called_once()

    @pytest.mark.asyncio
    async def test_dual_insert_supabase_primary_mode(self):
        with patch.object(storage, "STORAGE_MODE", "supabase_primary"):
            mock_table = MagicMock()
            mock_table.insert.return_value.execute.return_value = MagicMock()
            mock_client = MagicMock()
            mock_client.table.return_value = mock_table
            storage.bind_supabase(lambda: mock_client)

            with patch.object(storage, "d1_query", new_callable=AsyncMock) as mock_d1:
                mock_d1.return_value = {}
                result = await storage.dual_insert("mcqs", {"quiz_id": "xyz", "user_id": 2})
                assert result is True

    @pytest.mark.asyncio
    async def test_dual_insert_falls_back_to_supabase_when_d1_fails(self):
        with patch.object(storage, "STORAGE_MODE", "d1_primary"):
            with patch.object(storage, "d1_query", new_callable=AsyncMock) as mock_d1:
                mock_d1.return_value = {}  # D1 failure

                mock_table = MagicMock()
                mock_table.insert.return_value.execute.return_value = MagicMock()
                mock_client = MagicMock()
                mock_client.table.return_value = mock_table
                storage.bind_supabase(lambda: mock_client)

                result = await storage.dual_insert("mcqs", {"quiz_id": "fail", "user_id": 3})
                # primary_ok is False because D1 returned {}
                assert result is False
                # But supabase should have been called as fallback
                mock_client.table.assert_called_with("mcqs")


class TestDualGetMcq:
    """Tests for dual_get_mcq() — read from D1 then Supabase."""

    @pytest.mark.asyncio
    async def test_dual_get_mcq_from_d1(self):
        with patch.object(storage, "CF_D1_URL", "https://worker.test"):
            with patch.object(storage, "CF_D1_TOKEN", "tok"):
                with patch.object(storage, "STORAGE_MODE", "d1_primary"):
                    row = {"quiz_id": "abc", "mcqs": "[]"}
                    with patch.object(storage, "d1_query", new_callable=AsyncMock) as mock_d1:
                        mock_d1.return_value = {"results": [row]}
                        result = await storage.dual_get_mcq("abc")
                        assert result == row

    @pytest.mark.asyncio
    async def test_dual_get_mcq_fallback_to_supabase(self):
        with patch.object(storage, "CF_D1_URL", ""):
            with patch.object(storage, "CF_D1_TOKEN", ""):
                mock_result = MagicMock()
                mock_result.data = [{"quiz_id": "xyz", "mcqs": "[]"}]
                mock_table = MagicMock()
                mock_table.select.return_value.eq.return_value.execute.return_value = mock_result
                mock_client = MagicMock()
                mock_client.table.return_value = mock_table
                storage.bind_supabase(lambda: mock_client)

                result = await storage.dual_get_mcq("xyz")
                assert result == {"quiz_id": "xyz", "mcqs": "[]"}

    @pytest.mark.asyncio
    async def test_dual_get_mcq_returns_none_when_not_found(self):
        with patch.object(storage, "CF_D1_URL", ""):
            with patch.object(storage, "CF_D1_TOKEN", ""):
                mock_result = MagicMock()
                mock_result.data = []
                mock_table = MagicMock()
                mock_table.select.return_value.eq.return_value.execute.return_value = mock_result
                mock_client = MagicMock()
                mock_client.table.return_value = mock_table
                storage.bind_supabase(lambda: mock_client)

                result = await storage.dual_get_mcq("nonexistent")
                assert result is None


class TestBootstrapD1Schema:
    """Tests for bootstrap_d1_schema()."""

    @pytest.mark.asyncio
    async def test_bootstrap_does_nothing_when_disabled(self):
        with patch.object(storage, "CF_D1_URL", ""):
            with patch.object(storage, "d1_query", new_callable=AsyncMock) as mock_d1:
                await storage.bootstrap_d1_schema()
                mock_d1.assert_not_called()

    @pytest.mark.asyncio
    async def test_bootstrap_runs_schema_statements(self):
        with patch.object(storage, "CF_D1_URL", "https://worker.test"):
            with patch.object(storage, "CF_D1_TOKEN", "tok"):
                with patch.object(storage, "d1_query", new_callable=AsyncMock) as mock_d1:
                    mock_d1.return_value = {"ok": True}
                    await storage.bootstrap_d1_schema()
                    # Should have called d1_query for each SQL statement in D1_SCHEMA
                    assert mock_d1.call_count > 0
                    # Verify at least CREATE TABLE statements were executed
                    calls = [str(c) for c in mock_d1.call_args_list]
                    assert any("CREATE TABLE" in c for c in calls)


class TestEnforceQuotas:
    """Tests for enforce_quotas() — FIFO deletion when stores are full."""

    @pytest.mark.asyncio
    async def test_enforce_quotas_no_deletion_when_under_limit(self):
        with patch.object(storage, "CF_D1_URL", ""):
            with patch.object(storage, "CF_D1_TOKEN", ""):
                # Supabase count under limit
                mock_result = MagicMock()
                mock_result.count = 100
                mock_table = MagicMock()
                mock_table.select.return_value.limit.return_value.execute.return_value = mock_result
                mock_client = MagicMock()
                mock_client.table.return_value = mock_table
                storage.bind_supabase(lambda: mock_client)

                report = await storage.enforce_quotas()
                assert isinstance(report, dict)
                # Should cover all managed tables
                for table in storage.MANAGED_TABLES:
                    assert table in report

    @pytest.mark.asyncio
    async def test_enforce_quotas_deletes_when_over_limit(self):
        with patch.object(storage, "CF_D1_URL", ""):
            with patch.object(storage, "CF_D1_TOKEN", ""):
                with patch.object(storage, "SUPABASE_MAX_ROWS", 50):
                    # Supabase count over limit
                    mock_count_result = MagicMock()
                    mock_count_result.count = 200

                    mock_oldest_result = MagicMock()
                    mock_oldest_result.data = [{"id": i, "created_at": f"2024-01-{i:02d}"} for i in range(1, 51)]

                    mock_delete = MagicMock()
                    mock_delete.in_.return_value.execute.return_value = MagicMock()

                    mock_table = MagicMock()
                    mock_table.select.return_value.limit.return_value.execute.return_value = mock_count_result
                    mock_table.select.return_value.order.return_value.limit.return_value.execute.return_value = mock_oldest_result
                    mock_table.delete.return_value = mock_delete

                    mock_client = MagicMock()
                    mock_client.table.return_value = mock_table
                    storage.bind_supabase(lambda: mock_client)

                    report = await storage.enforce_quotas()
                    assert isinstance(report, dict)
