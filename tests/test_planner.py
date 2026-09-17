"""Tests for the multi-step planner state machine."""

from __future__ import annotations

from speaksql.introspect import ColumnInfo, ForeignKeyInfo, SchemaList, TableInfo
from speaksql.llm import LLMError, MockProvider
from speaksql.planner import (
    Done,
    run_planner,
)


def _schema() -> SchemaList:
    return SchemaList(
        tables=(
            TableInfo(None, None, "users", columns=(
                ColumnInfo("id", "INTEGER", is_primary_key=True),
                ColumnInfo("email", "TEXT"),
            )),
            TableInfo(None, None, "orders", columns=(
                ColumnInfo("id", "INTEGER", is_primary_key=True),
                ColumnInfo("user_id", "INTEGER"),
            )),
        ),
        foreign_keys=(ForeignKeyInfo("orders", "user_id", "users", "id"),),
    )


def test_standard_mode_skips_identify_stages():
    """advanced=False should run only generate + 2 validate stages."""
    result = run_planner(
        "show me all users", advanced=False, provider=MockProvider("SELECT 1")
    )
    assert "identify_tables" not in result.state.stages_run
    assert "identify_columns" not in result.state.stages_run
    assert "generate_sql" in result.state.stages_run
    assert "syntactic_validate" in result.state.stages_run
    assert "business_validate" in result.state.stages_run


def test_advanced_mode_runs_all_stages():
    """advanced=True should run identify_tables + identify_columns too."""
    result = run_planner(
        "users with their orders",
        advanced=True,
        schema=_schema(),
        provider=MockProvider("SELECT 1"),
    )
    assert result.state.stages_run[0] == "identify_tables"
    assert "identify_columns" in result.state.stages_run
    assert "generate_sql" in result.state.stages_run


def test_advanced_picks_relevant_tables_from_schema():
    """IdentifyTables should populate state.table_hint from FK-aware planner."""
    result = run_planner(
        "users with their orders",
        advanced=True,
        schema=_schema(),
        provider=MockProvider("SELECT 1"),
    )
    assert "users" in result.state.table_hint
    assert "orders" in result.state.table_hint


def test_advanced_picks_relevant_columns():
    """IdentifyColumns should list columns from the chosen tables."""
    result = run_planner(
        "users with their orders",
        advanced=True,
        schema=_schema(),
        provider=MockProvider("SELECT 1"),
    )
    assert "id" in result.state.column_hint
    assert "user_id" in result.state.column_hint


def test_business_validate_catches_unknown_columns():
    """A column reference outside approved tables should flag an error."""
    # Use a fake provider that returns the broken SQL we want to test against.
    class _Fake:
        def complete(self, messages):  # type: ignore[no-untyped-def]
            return "SELECT nothing_burger FROM users"

        def stream(self, messages):  # type: ignore[no-untyped-def]
            return iter([self.complete(messages)])

    result = run_planner(
        "give me nothing_burger column from users",
        advanced=True,
        schema=_schema(),
        provider=_Fake(),
    )
    # The fake returns SQL referencing an unknown column; business validate
    # should flag it.
    assert any("nothing_burger" in e for e in result.state.errors)


def test_business_validate_passes_for_known_columns():
    class _Fake:
        def complete(self, messages):  # type: ignore[no-untyped-def]
            return "SELECT id, email FROM users"

        def stream(self, messages):  # type: ignore[no-untyped-def]
            return iter([self.complete(messages)])

    result = run_planner(
        "ok",
        advanced=True,
        schema=_schema(),
        provider=_Fake(),
    )
    assert result.state.errors == []


def test_retry_loop_on_parse_error():
    """A first attempt with a parse error should trigger retry."""

    # A fake provider that returns broken SQL first, then valid SQL.
    class _Provider:
        calls = 0

        def complete(self, messages):  # type: ignore[no-untyped-def]
            _Provider.calls += 1
            if _Provider.calls == 1:
                return "BROKEN GARBAGE FROM events"
            return "SELECT id, email FROM users"

        def stream(self, messages):  # type: ignore[no-untyped-def]
            return iter([self.complete(messages)])

    _Provider.calls = 0
    result = run_planner(
        "test",
        advanced=True,
        schema=_schema(),
        provider=_Provider(),
        max_retries=2,
    )
    # Retry should have been triggered at least once
    assert result.state.retry_count >= 1
    # Final state should have the working SQL
    assert "FROM users" in result.state.canonical_sql


def test_retry_budget_exhaustion_returns_last_attempt():
    """A consistently broken provider should hit DONE with the last attempt."""

    class _AlwaysBad:
        def complete(self, messages):  # type: ignore[no-untyped-def]
            return "BROKEN"

        def stream(self, messages):  # type: ignore[no-untyped-def]
            return iter([self.complete(messages)])

    result = run_planner(
        "test",
        advanced=True,
        schema=_schema(),
        provider=_AlwaysBad(),
    )
    # Should have exhausted retries and returned
    assert isinstance(result.events[-1], Done)
    assert result.state.canonical_sql == "BROKEN"


def test_provider_llm_error_short_circuits():
    """An LLMError from the provider should surface, not loop forever."""

    class _Boom:
        def complete(self, messages):  # type: ignore[no-untyped-def]
            raise LLMError("rate limited")

        def stream(self, messages):  # type: ignore[no-untyped-def]
            raise LLMError("rate limited")

    result = run_planner("q", provider=_Boom())
    # Should bail with an error recorded
    assert result.state.errors
    assert any("rate limited" in e for e in result.state.errors)


def test_examples_pass_through_to_llm_layer():
    """Retrieved examples should reach the LLM provider's messages."""
    captured = []

    class _CaptureProvider:
        def complete(self, messages):  # type: ignore[no-untyped-def]
            captured.extend(messages)
            return "SELECT 1"

        def stream(self, messages):  # type: ignore[no-untyped-def]
            return iter([self.complete(messages)])

    examples = [
        ("count of orders", "SELECT COUNT(*) FROM orders"),
        ("monthly revenue", "SELECT DATE_TRUNC('month', ts) FROM events"),
    ]
    run_planner(
        "count orders",
        advanced=False,
        examples=examples,
        provider=_CaptureProvider(),
    )
    # The two example Q's should appear in the messages somewhere
    msgs_text = "\n".join(m["content"] for m in captured)
    assert "count of orders" in msgs_text
    assert "monthly revenue" in msgs_text


def test_instructions_pass_through_to_llm_layer():
    """Per-source instructions should appear in the system prompt."""
    captured = []

    class _CaptureProvider:
        def complete(self, messages):  # type: ignore[no-untyped-def]
            captured.extend(messages)
            return "SELECT 1"

        def stream(self, messages):  # type: ignore[no-untyped-def]
            return iter([self.complete(messages)])

    run_planner(
        "test",
        advanced=False,
        instructions="Always use lowercase aliases and qualify column names",
        provider=_CaptureProvider(),
    )
    sys_msg = captured[0]["content"]
    assert "lowercase aliases" in sys_msg


def test_advanced_returns_done_event():
    result = run_planner("test", advanced=True, provider=MockProvider("SELECT 1"))
    assert isinstance(result.events[-1], Done)


def test_events_trace_correctly():
    result = run_planner(
        "test", advanced=False, provider=MockProvider("SELECT 1")
    )
    # We expect ContinueTo + ContinueTo + Done for standard mode
    types = [type(e).__name__ for e in result.events]
    assert "ContinueTo" in types
    assert types[-1] == "Done"


def test_planner_state_carries_through():
    """state is the same object across stages; stages_run accumulates."""
    result = run_planner(
        "test", advanced=True, schema=_schema(),
        provider=MockProvider("SELECT id FROM users"),
    )
    # Stages should be unique in their first occurrence; later retries
    # append the same name.
    assert result.state.stages_run[0] in {
        "identify_tables", "identify_columns", "generate_sql"
    }