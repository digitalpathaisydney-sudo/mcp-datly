# datly-mcp

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![MCP](https://img.shields.io/badge/MCP-server-7B3FE4)

An [MCP](https://modelcontextprotocol.io) server that lets an AI assistant
(e.g. Claude Code) **read and edit your Datly database diagrams while you watch
the changes appear live** in the open editor tab.

It talks only to your Datly Django backend over HTTP; the editor tab subscribes
to a WebSocket and refetches whenever the MCP makes a change.

> **Status:** Milestone 4 — read + session tools, coarse mutations (apply DBML /
> SQL DDL), the 8 fine schema tools, 3 visual tools, and area-focus with FK
> shadows + an out-of-scope write guard. 22 tools.

## Install

```bash
# editable install from this repo (until published to PyPI):
pip install -e .
# provides the `datly-mcp` console script
```

## Connect it

1. In Datly, open **`/account/mcp-tokens`** → **Generate token** → copy the
   `.mcp.json` snippet (the launch token is shown once and expires in ~60s).
2. Paste it into your MCP config (e.g. `~/.claude.json` or a project `.mcp.json`):

   ```json
   {
     "mcpServers": {
       "datly": {
         "command": "datly-mcp",
         "env": {
           "DATLY_MCP_LAUNCH_TOKEN": "lt_…",
           "DATLY_API_URL": "http://localhost:8005/api"
         }
       }
     }
   }
   ```

3. Restart the assistant. On first run the MCP redeems the launch token for its
   own access+refresh pair and caches it at `~/.datly-mcp/credentials.json`
   (chmod 600). After that the launch token is no longer needed; the MCP
   refreshes its own session automatically.

   If the launch token expires before first boot, run
   `./bootstrap-creds.sh <launch_token>` right after minting — it redeems the
   token into the credentials cache so the next start is race-free. A running
   server also self-heals: if its refresh fails it re-reads the cache, so
   re-running `bootstrap-creds.sh` recovers it without a restart.

### Environment

| Var | Required | Purpose |
|---|---|---|
| `DATLY_API_URL` | yes | Datly Django REST root (e.g. `http://localhost:8005/api`) |
| `DATLY_MCP_LAUNCH_TOKEN` | first run only | single-use bootstrap token from the tokens page |
| `DATLY_MCP_LOG_LEVEL` | no | log level to stderr (default `INFO`) |

The MCP's only network dependency is `DATLY_API_URL` — token refresh is proxied
through Datly, so the MCP never needs the Auth Hub URL.

## Tools (22)

**Read + session (9):** `list_my_diagrams`, `set_active_diagram`,
`get_active_diagram_id`, `create_diagram`, `get_diagram`, `list_areas`,
`set_active_area`, `get_active_area`, `clear_active_area`.

**Coarse mutations (2):** `apply_dbml(dbml_text)`,
`apply_sql_ddl(sql_text, source_db_type)` — merge/upsert a whole DBML or SQL DDL
blob (non-destructive: never deletes objects absent from the input).

**Fine schema (8):** `add_table`, `update_table`, `delete_table`, `add_field`,
`update_field`, `delete_field`, `add_relationship`, `delete_relationship`.

**Visual (3):** `add_area`, `assign_table_to_area`, `add_note`.

Every tool response carries a `_context` footer (`active_diagram`,
`active_area`, `scope`) so the assistant always knows its scope. While an area
is active, `get_diagram` returns only that area's tables plus read-only FK
shadows of out-of-scope tables they reference, and any edit to an out-of-scope
table is rejected with `OUT_OF_SCOPE` (switch areas or `clear_active_area()`).

## Example session

```
list my diagrams
set active diagram <id>          → name + table/area counts
apply this DBML: Table posts { id integer [pk] author_id integer [ref: > users.id] }
set active area Billing          → scope narrows to that area
add a table subscriptions        → lands in Billing, FK shadows resolve
```

## How it works

```
Claude Code ──stdio──> datly-mcp ──HTTP(DATLY_API_URL)──> Datly Django
                                                              │ broadcast
                                          editor tab <──WebSocket──┘ (live refetch)
```

The MCP holds its own access/refresh pair (independent of the web session) and
stamps every write with `X-Initiated-By: mcp`, so the editor tab knows the
change came from the assistant and refetches in under a second.

## License

MIT — see [LICENSE](LICENSE).
