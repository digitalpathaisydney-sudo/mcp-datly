"""Coarse mutation tools — apply a whole DBML (or SQL DDL) blob at once.

These complement the fine schema tools: an assistant can either hand-edit one
field, or send a chunk of DBML and let the server diff+apply it. Both forward
``X-Initiated-By: mcp`` (set by the client) and a fresh ``X-Idempotency-Key`` so
a retried call doesn't double-apply (server honours it for 60s).
"""
from __future__ import annotations

import uuid

from ..server import mcp
from ..state import MCPSession
from .read import _run, _require_active_diagram


def _idempotency_key() -> str:
    return str(uuid.uuid4())


@mcp.tool()
def apply_dbml(dbml_text: str) -> dict:
    """Apply DBML to the active diagram and update the open editor tab live.

    Merge/upsert semantics: every table, field, relationship, and enum named
    in the DBML is created or updated; objects absent from the DBML are left
    untouched (the DBML is treated as a partial edit, not the full schema).
    Returns a summary of what changed. Requires an active diagram.
    """
    def _impl(session: MCPSession) -> dict:
        diagram_id = _require_active_diagram(session)
        result = session.client.request(
            "POST",
            f"/diagrams/{diagram_id}/apply-dbml/",
            json={"dbml_text": dbml_text},
            headers={"X-Idempotency-Key": _idempotency_key()},
        )
        return {"success": True, "applied": (result or {}).get("applied", {})}

    return _run(_impl)


@mcp.tool()
def apply_sql_ddl(sql_text: str, source_db_type: str = "postgres") -> dict:
    """Apply SQL DDL (CREATE TABLE …) to the active diagram and update the open
    editor tab live. Same merge/upsert semantics as ``apply_dbml`` but parsed
    from SQL. ``source_db_type`` is the SQL dialect (default ``postgres``).
    Requires an active diagram.
    """
    def _impl(session: MCPSession) -> dict:
        diagram_id = _require_active_diagram(session)
        result = session.client.request(
            "POST",
            f"/diagrams/{diagram_id}/apply-sql-ddl/",
            json={"sql_text": sql_text, "source_db_type": source_db_type},
            headers={"X-Idempotency-Key": _idempotency_key()},
        )
        return {"success": True, "applied": (result or {}).get("applied", {})}

    return _run(_impl)
