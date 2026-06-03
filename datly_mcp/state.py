"""In-memory session state for the Datly MCP.

Only credentials persist to disk (see credentials.py). The *scope* — which
diagram and area the AI is working in — is in-memory only, so a Claude Code
restart forces an explicit ``set_active_diagram`` / ``set_active_area`` again.
That's deliberate: it avoids stale-context surprises across sessions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .client import DatlyClient


@dataclass
class MCPSession:
    client: DatlyClient
    active_diagram_id: Optional[str] = None
    active_diagram_name: Optional[str] = None
    # Area ids are UUID strings in Datly's schema (NOT ints).
    active_area_id: Optional[str] = None
    active_area_name: Optional[str] = None

    def set_diagram(self, diagram_id: Optional[str], name: Optional[str]) -> None:
        """Switch active diagram and clear any area scope (areas belong to a
        specific diagram, so they can't survive a diagram switch).
        """
        self.active_diagram_id = diagram_id
        self.active_diagram_name = name
        self.active_area_id = None
        self.active_area_name = None

    def set_area(self, area_id: Optional[str], name: Optional[str]) -> None:
        self.active_area_id = area_id
        self.active_area_name = name

    def context(self) -> dict:
        """The ``_context`` footer attached to every tool result (spec §9).

        ``scope`` is ``no_diagram`` → ``full`` → ``filtered`` as the AI narrows
        focus, so it can read its own scope off every response.
        """
        if self.active_diagram_id and self.active_area_id:
            scope = "filtered"
        elif self.active_diagram_id:
            scope = "full"
        else:
            scope = "no_diagram"

        active_diagram = (
            {"id": self.active_diagram_id, "name": self.active_diagram_name}
            if self.active_diagram_id
            else None
        )
        active_area = (
            {"id": self.active_area_id, "name": self.active_area_name}
            if self.active_area_id
            else None
        )
        return {
            "active_diagram": active_diagram,
            "active_area": active_area,
            "scope": scope,
        }
