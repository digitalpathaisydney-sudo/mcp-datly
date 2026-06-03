"""Tool package. Importing it registers every ``@mcp.tool()`` with the global
FastMCP instance. ``server.main()`` imports this after the session is built.
"""
from . import read  # noqa: F401
from . import coarse  # noqa: F401
from . import schema  # noqa: F401
from . import visual  # noqa: F401
