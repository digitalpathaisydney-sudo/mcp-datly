"""Compact representations for read tools (context-budget hygiene).

The DRF diagram serializer is faithful but heavy: every field carries 17 keys
(``type_id``, ``order``, ``created_at``, plus every boolean even when it's the
default). For a 29-table diagram that's ~200 KB / ~25 k tokens in a single tool
result — enough to crowd the model's context.

These helpers re-shape that payload for the assistant WITHOUT losing anything it
needs to reason or to act:

* IDs are kept everywhere (the fine tools update/delete by id).
* Default-valued booleans are dropped (``primary_key=False``, ``nullable=True``,
  …) — absence carries the default. The one inversion: a non-nullable column is
  emitted as ``not_null: true`` (DBML convention: a column is nullable unless
  marked), so absence of the marker unambiguously means nullable.
* ``type_id`` (a numeric the model never reasons over), ``order``, ``created_at``
  and geometry (``x``/``y``/``width``/``color``/…) are dropped — none affect
  schema reasoning.

Nothing here hits the network; callers pass already-fetched DRF dicts.
"""
from __future__ import annotations

from typing import Any, Optional


def slim_field(f: dict) -> dict:
    """A DBField dict → compact form (id + name + type + only non-defaults)."""
    out: dict[str, Any] = {
        "id": f.get("id"),
        "name": f.get("name"),
        "type": f.get("type_name"),  # collapse type_id/type_name → one string
    }
    if f.get("primary_key"):
        out["pk"] = True
    if f.get("unique"):
        out["unique"] = True
    if f.get("nullable") is False:  # default is True → only mark NOT NULL
        out["not_null"] = True
    if f.get("increment"):
        out["increment"] = True
    if f.get("is_array"):
        out["array"] = True
    if f.get("character_maximum_length") is not None:
        out["len"] = f["character_maximum_length"]
    if f.get("precision") is not None:
        out["precision"] = f["precision"]
    if f.get("scale") is not None:
        out["scale"] = f["scale"]
    if f.get("default") not in (None, ""):
        out["default"] = f["default"]
    if f.get("collation"):
        out["collation"] = f["collation"]
    if f.get("comments"):
        out["note"] = f["comments"]
    return out


def slim_index(i: dict) -> dict:
    out: dict[str, Any] = {"id": i.get("id"), "columns": i.get("field_ids")}
    if i.get("name"):
        out["name"] = i["name"]
    if i.get("unique"):
        out["unique"] = True
    return out


def _schema_meaningful(schema: Optional[str]) -> bool:
    return bool(schema) and schema not in ("public",)


def slim_table(t: dict) -> dict:
    """A DBTable dict → compact form. Drops geometry/order/timestamps; keeps
    fields (slimmed), and indexes/flags only when present/non-default."""
    out: dict[str, Any] = {
        "id": t.get("id"),
        "name": t.get("name"),
        "fields": [slim_field(f) for f in (t.get("fields") or [])],
    }
    if _schema_meaningful(t.get("schema")):
        out["schema"] = t["schema"]
    if t.get("is_view"):
        out["view"] = True
    if t.get("is_materialized_view"):
        out["materialized"] = True
    if t.get("comments"):
        out["note"] = t["comments"]
    indexes = [slim_index(i) for i in (t.get("indexes") or [])]
    if indexes:
        out["indexes"] = indexes
    return out


def slim_relationship(r: dict) -> dict:
    out: dict[str, Any] = {
        "id": r.get("id"),
        "from": [r.get("source_table"), r.get("source_field_id")],
        "to": [r.get("target_table"), r.get("target_field_id")],
        "card": f"{r.get('source_cardinality')}-{r.get('target_cardinality')}",
    }
    if r.get("name"):
        out["name"] = r["name"]
    return out


def slim_area(a: dict) -> dict:
    return {"id": a.get("id"), "name": a.get("name")}


def slim_note(n: dict) -> dict:
    content = n.get("content") or ""
    if len(content) > 280:
        content = content[:280] + "…"
    return {"id": n.get("id"), "content": content}


def slim_diagram(d: dict) -> dict:
    """Whole-diagram compact view — same information the assistant needs, ~3-4×
    smaller than the raw DRF payload."""
    return {
        "id": d.get("id"),
        "name": d.get("name"),
        "database_type": d.get("database_type"),
        "tables": [slim_table(t) for t in (d.get("tables") or [])],
        "relationships": [
            slim_relationship(r) for r in (d.get("relationships") or [])
        ],
        "areas": [slim_area(a) for a in (d.get("areas") or [])],
        "notes": [slim_note(n) for n in (d.get("notes") or [])],
    }


def slim_area_view(view: dict) -> dict:
    """Compact form of the area-scoped view (build_area_scoped_view):
    in-scope tables slimmed, out-of-scope FK shadows kept read-only."""
    focus = view.get("focus_area") or {}
    return {
        "focus_area": slim_area(focus) if focus else None,
        "in_scope": [slim_table(t) for t in (view.get("in_scope") or [])],
        "shadows": [slim_table(s) for s in (view.get("out_of_scope_shadows") or [])],
        "relationships": [
            slim_relationship(r) for r in (view.get("relationships") or [])
        ],
        "counts": view.get("counts"),
    }


def outline_diagram(d: dict) -> dict:
    """A table-of-contents: per-table name + field count + PK + outgoing FK
    targets, plus area list and totals. Tiny — meant to be read first so the
    assistant can drill into only the tables it needs via get_table()."""
    tables = d.get("tables") or []
    rels = d.get("relationships") or []

    name_by_id = {t.get("id"): t.get("name") for t in tables}
    # source_table id → set of target table names it references
    refs: dict[Any, list[str]] = {}
    for r in rels:
        src = r.get("source_table")
        tgt_name = name_by_id.get(r.get("target_table"))
        if tgt_name:
            refs.setdefault(src, [])
            if tgt_name not in refs[src]:
                refs[src].append(tgt_name)

    table_rows = []
    total_fields = 0
    for t in tables:
        fields = t.get("fields") or []
        total_fields += len(fields)
        pk = [f.get("name") for f in fields if f.get("primary_key")]
        row: dict[str, Any] = {
            "id": t.get("id"),
            "name": t.get("name"),
            "fields": len(fields),
        }
        if pk:
            row["pk"] = pk
        out_refs = refs.get(t.get("id"))
        if out_refs:
            row["refs"] = out_refs
        table_rows.append(row)

    return {
        "diagram": {
            "id": d.get("id"),
            "name": d.get("name"),
            "database_type": d.get("database_type"),
        },
        "tables": table_rows,
        "areas": [slim_area(a) for a in (d.get("areas") or [])],
        "counts": {
            "tables": len(tables),
            "fields": total_fields,
            "relationships": len(rels),
            "areas": len(d.get("areas") or []),
            "notes": len(d.get("notes") or []),
        },
    }


def find_table(d: dict, name_or_id: str) -> Optional[dict]:
    """Resolve a table within a diagram payload by id or (case-insensitive)
    name. Returns the raw DRF table dict, or None."""
    key = (name_or_id or "").strip()
    low = key.lower()
    for t in d.get("tables") or []:
        if str(t.get("id")) == key or (t.get("name") or "").lower() == low:
            return t
    return None
