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
> shadows + an out-of-scope write guard. 24 tools.

## Install

Install the `datly-mcp` command on the machine where your assistant runs. We
recommend [pipx](https://pipx.pypa.io) (installs it isolated, on your PATH):

```bash
pipx install git+https://github.com/digitalpathaisydney-sudo/mcp-datly.git
# once it's on PyPI:  pipx install datly-mcp
```

(For local development of this package: `pip install -e .` from a checkout.)

## Connect it

1. In Datly, open **`/account/mcp-tokens`** → **Create API key** → name it
   (e.g. "Laptop") → copy the `.mcp.json` snippet. The key (`dlymcp_…`) is
   shown **once**.
2. Paste it into your MCP config (e.g. `~/.claude.json` or a project `.mcp.json`):

   ```json
   {
     "mcpServers": {
       "datly": {
         "command": "datly-mcp",
         "env": {
           "DATLY_MCP_API_KEY": "dlymcp_…",
           "DATLY_API_URL": "https://datly.tech/api"
         }
       }
     }
   }
   ```

3. Restart the assistant. That's it — no on-disk credential cache, no refresh
   dance, no race against a launch-token TTL. The key is long-lived; revoke it
   from `/account/mcp-tokens` when you're done.

### Workspace scope (optional)

If you want the MCP scoped to an org workspace instead of Personal, pick the
workspace in the create-key dialog and the generated snippet will include:

```json
"DATLY_MCP_WORKSPACE_ORG_ID": "7"
```

The key is bound to that workspace at mint time — every MCP request is filtered
to diagrams in that workspace (so a "Personal" key can't see org diagrams and
vice-versa).

### Environment

| Var | Required | Purpose |
|---|---|---|
| `DATLY_API_URL` | yes | Datly REST root (e.g. `https://datly.tech/api`) |
| `DATLY_MCP_API_KEY` | yes (modern) | long-lived `dlymcp_…` key from the tokens page |
| `DATLY_MCP_WORKSPACE_ORG_ID` | no | scope MCP calls to an org workspace |
| `DATLY_MCP_LOG_LEVEL` | no | log level to stderr (default `INFO`) |

The MCP's only network dependency is `DATLY_API_URL` — the server holds the
Hub session, so the MCP never needs the Auth Hub URL.

> **Legacy installs** still using `DATLY_MCP_LAUNCH_TOKEN` +
> `~/.datly-mcp/credentials.json` keep working for back-compat; the server
> logs a deprecation warning on each start. Re-mint via the new flow when
> convenient.

## Tools (24)

**Read + session (11):** `list_my_diagrams`, `set_active_diagram`,
`get_active_diagram_id`, `create_diagram`, `get_diagram_outline`, `get_table`,
`get_diagram`, `list_areas`, `set_active_area`, `get_active_area`,
`clear_active_area`.

Reads are context-budget-aware: `get_diagram` returns a slimmed snapshot (each
field carries only its non-default modifiers — ~⅓ the size of the raw payload),
and on a large diagram you should read `get_diagram_outline()` (a tiny
table-of-contents) first, then pull only the tables you need with
`get_table(name)`. `get_diagram(verbose=True)` returns the full untrimmed
payload when you really need geometry/timestamps.

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
