"""Multi-step planner for NL→SQL.

A simplified, dependency-free version of the Semantic Kernel state
machine approach used by Microsoft for Fabric NL2SQL "Advanced". Each
stage is a pure function (state_in, ctx) -> (state_out, event); events
decide which stage runs next; the planner loops until an event says
DONE or we hit the retry budget.

Stages (default flow):

    IdentifyTables  -> IdentifyColumns  -> GenerateSQL
    GenerateSQL     -> SyntacticValidate -> BusinessValidate -> DONE
    SyntacticValidate (on parse error) -> GenerateSQL (with feedback)
    BusinessValidate (on rejection)    -> GenerateSQL (with feedback)

A single-pass planner is just GenerateSQL -> SyntacticValidate ->
BusinessValidate -> DONE; the two Identify* stages are skipped when
``advance=False`` (default) for the "standard" mode that Fabric's GA
NL2SQL also uses.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from speaksql.examples import Example, Retriever
from speaksql.introspect import SchemaList
from speaksql.llm import (
    LLMError,
    LLMProvider,
    llm_to_canonical,
)
from speaksql.schema_aware import SchemaHint, plan_for_question

# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class Event:
    """Marker base class. Each stage emits one of these."""


@dataclass(frozen=True)
class ContinueTo(Event):
    name: str  # next stage


@dataclass(frozen=True)
class Retry(Event):
    name: str  # stage to retry
    feedback: str


@dataclass(frozen=True)
class Done(Event):
    pass


# ---------------------------------------------------------------------------
# Plan state
# ---------------------------------------------------------------------------


@dataclass
class PlannerState:
    """Mutable state passed between stages."""

    question: str
    canonical_sql: str = ""
    schema: SchemaList | None = None
    table_hint: str = ""
    column_hint: str = ""
    last_attempt_sql: str = ""
    retry_count: int = 0
    errors: list[str] = field(default_factory=list)
    # Carried through to the caller for observability.
    stages_run: list[str] = field(default_factory=list)


@dataclass
class PlannerConfig:
    """Per-call config."""

    advanced: bool = False
    max_retries: int = 2
    instructions: str | None = None
    examples: Sequence[tuple[str, str]] | None = None
    schema_hint: str | None = None
    provider: LLMProvider | None = None


@dataclass(frozen=True)
class PlannerResult:
    state: PlannerState
    events: list[Event]


# ---------------------------------------------------------------------------
# Stages (each takes state + cfg -> (state, Event))
# ---------------------------------------------------------------------------


def stage_identify_tables(
    state: PlannerState, cfg: PlannerConfig
) -> PlannerState:
    """Pick candidate tables from the question + schema.

    Only runs in advanced mode. Uses the FK-aware planner's table
    scoring so we don't need a second LLM call just for table names.
    """
    state.stages_run.append("identify_tables")
    if state.schema is None:
        state.table_hint = "(no schema — using schema_hint fallback)"
        return state
    hint = plan_for_question(state.question, state.schema)
    state.table_hint = ", ".join(hint.tables)
    return state


def stage_identify_columns(state: PlannerState, cfg: PlannerConfig) -> PlannerState:
    """Annotate the table hint with relevant columns.

    Cheap heuristic — for each chosen table, list its columns. The
    LLM gets a smaller "what columns matter" preamble than dumping
    the full schema. Skipped in non-advanced mode.
    """
    state.stages_run.append("identify_columns")
    if state.schema is None or not state.table_hint:
        return state
    lines: list[str] = []
    chosen = {t.strip() for t in state.table_hint.split(",") if t.strip()}
    for t in state.schema.tables:
        if t.name not in chosen:
            continue
        cols = ", ".join(c.name for c in t.columns)
        lines.append(f"- {t.name}: {cols}")
    state.column_hint = "\n".join(lines)
    return state


def stage_generate_sql(state: PlannerState, cfg: PlannerConfig) -> PlannerState:
    """Call the LLM to translate NL -> canonical SQL.

    Re-callable: subsequent calls include the previous attempt and
    any feedback in the schema_hint block so the model can correct.
    """
    state.stages_run.append("generate_sql")
    schema_hint = cfg.schema_hint or ""
    if state.table_hint:
        schema_hint += f"\n\nRelevant tables: {state.table_hint}"
    if state.column_hint:
        schema_hint += f"\n\nRelevant columns:\n{state.column_hint}"
    if state.last_attempt_sql and state.errors:
        schema_hint += "\n\nPrevious attempt:\n```sql\n"
        schema_hint += state.last_attempt_sql
        schema_hint += "\n```\n\nErrors:\n- " + "\n- ".join(state.errors)
        schema_hint += "\n\nFix the SQL so it parses and addresses the question."

    canonical = llm_to_canonical(
        state.question,
        provider=cfg.provider,
        schema_hint=schema_hint or None,
        instructions=cfg.instructions,
        examples=cfg.examples,
    )
    state.canonical_sql = canonical
    state.last_attempt_sql = canonical
    return state


def stage_syntactic_validate(state: PlannerState, cfg: PlannerConfig) -> PlannerState:
    """Parse the SQL through SQLGlot. On error, feed back the parser message."""
    state.stages_run.append("syntactic_validate")
    if not state.canonical_sql:
        state.errors.append("empty SQL returned by LLM")
        return state
    try:
        sqlglot.parse_one(state.canonical_sql, dialect=None)
        state.errors = []  # clear prior errors on success
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError) as exc:
        state.errors.append(f"parse error: {exc}")
    return state


def stage_business_validate(state: PlannerState, cfg: PlannerConfig) -> PlannerState:
    """Lightweight domain checks beyond SQL syntax.

    Right now: column references must exist on tables the FK-aware
    planner picked. (Future: row-count sanity, query-cost estimates,
    purview-style DLP rules.)
    """
    state.stages_run.append("business_validate")
    if state.schema is None or not state.canonical_sql:
        return state
    chosen = {t.strip() for t in state.table_hint.split(",") if t.strip()}
    if not chosen:
        return state
    try:
        ast = sqlglot.parse_one(state.canonical_sql, dialect=None)
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError):
        return state  # syntactic stage will retry

    valid_columns: set[str] = set()
    for t in state.schema.tables:
        if t.name in chosen:
            for c in t.columns:
                valid_columns.add(c.name.lower())

    refs: set[str] = set()
    for node in ast.walk():
        if isinstance(node, exp.Column):
            refs.add(node.name.lower())

    unknown = refs - valid_columns
    if unknown:
        state.errors.append(
            f"column references not in approved tables: {sorted(unknown)}"
        )
    else:
        state.errors = []
    return state


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


_STAGES = {
    "identify_tables": stage_identify_tables,
    "identify_columns": stage_identify_columns,
    "generate_sql": stage_generate_sql,
    "syntactic_validate": stage_syntactic_validate,
    "business_validate": stage_business_validate,
}


def _decide_next(stage_name: str, state: PlannerState, cfg: PlannerConfig) -> Event:
    if state.errors and state.retry_count < cfg.max_retries:
        state.retry_count += 1
        return Retry(name="generate_sql", feedback="; ".join(state.errors))
    if state.errors and state.retry_count >= cfg.max_retries:
        # Budget exhausted. Return what we have; let the caller decide.
        return Done()
    # Happy path
    if stage_name == "identify_tables":
        return ContinueTo("identify_columns")
    if stage_name == "identify_columns":
        return ContinueTo("generate_sql")
    if stage_name == "generate_sql":
        return ContinueTo("syntactic_validate")
    if stage_name == "syntactic_validate":
        return ContinueTo("business_validate")
    if stage_name == "business_validate":
        return Done()
    return Done()


def run_planner(
    question: str,
    *,
    schema: SchemaList | None = None,
    advanced: bool = False,
    max_retries: int = 2,
    instructions: str | None = None,
    examples: Sequence[tuple[str, str]] | None = None,
    schema_hint: str | None = None,
    provider: LLMProvider | None = None,
) -> PlannerResult:
    """Drive the multi-step planner to completion.

    Returns a `PlannerResult` whose `state.canonical_sql` is the final
    SQL (or the last attempt if the retry budget was exhausted).
    The `stages_run` list is useful for debug logs.
    """
    cfg = PlannerConfig(
        advanced=advanced,
        max_retries=max_retries,
        instructions=instructions,
        examples=examples,
        schema_hint=schema_hint,
        provider=provider,
    )
    state = PlannerState(question=question, schema=schema)
    events: list[Event] = []

    if advanced:
        next_stage = "identify_tables"
    else:
        next_stage = "generate_sql"

    while next_stage:
        stage_fn = _STAGES[next_stage]
        try:
            state = stage_fn(state, cfg)
        except LLMError as exc:
            state.errors.append(f"LLM error: {exc}")
            state.canonical_sql = state.last_attempt_sql
            return PlannerResult(state=state, events=events)

        event = _decide_next(next_stage, state, cfg)
        events.append(event)
        if isinstance(event, Done):
            break
        if isinstance(event, (ContinueTo, Retry)):
            next_stage = event.name
        else:
            break

    return PlannerResult(state=state, events=events)


__all__ = [
    "ContinueTo",
    "Done",
    "Event",
    "PlannerConfig",
    "PlannerResult",
    "PlannerState",
    "Retry",
    "run_planner",
]


# Silence an unused-import warning while keeping the type available for
# downstream consumers.
_ = (Example, Retriever, SchemaHint)