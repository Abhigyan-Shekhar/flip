# Optional Waggle memory

SwitchCrew memory is disabled by default. Enable it explicitly with
`SWITCHCREW_MEMORY_ENABLED=true`. The stable project defaults to
`github.com/Abhigyan-Shekhar/flip`; use `SWITCHCREW_MEMORY_PROJECT` to isolate another
codebase. Prime and recall filter only by project, sharing curated knowledge
across roles and tasks. Observations also carry `agent_id=switchcrew:<role>` and
the workflow session identifier as provenance; keep those stable across retries
and restarts. The development assistant uses the same default project identity,
so its memories can be recalled when the same Waggle store is used. Set
`SWITCHCREW_MEMORY_PROJECT` to a distinct stable identifier to separate product
memory from development assistant memory. Project filtering is a retrieval
boundary; deploy a separate Waggle store or tenant when access isolation is needed.

Choose exactly one configured transport:

- `SWITCHCREW_WAGGLE_COMMAND='["/path/to/waggle/.venv/bin/python","-m","waggle.entrypoints.server_only","--quiet"]'`
  starts the verified upstream server-only Python entrypoint through the official
  MCP SDK. Replace the Python path with the interpreter of your installed Waggle
  environment; the example path is a placeholder.
- `SWITCHCREW_WAGGLE_URL=https://your-waggle-host/mcp` connects to an already running
  MCP Streamable HTTP endpoint. Use the actual deployment endpoint; SwitchCrew
  does not invent a REST API or provision a server. Optional
  `SWITCHCREW_WAGGLE_TOKEN` is sent as an Authorization Bearer header. Remote HTTP
  requires TLS; plain HTTP is accepted only on loopback.

SwitchCrew never changes the installed Codex plugin or its configuration. The
stdio child receives only PATH, HOME, LANG and TMPDIR; configure Waggle's own
storage/provider settings separately (or through a dedicated operator wrapper).
SwitchCrew `.env` values and `WAGGLE_*` environment variables are not inherited;
use a wrapper that explicitly loads the necessary Waggle configuration if needed.
The inherited HOME can select the operator's existing default Waggle store; choose
an isolated store in the wrapper when shared personal memory is inappropriate.
Waggle owns storage; deployment operators must arrange its persistence and access
controls. The bundled plugin currently supports stdio only; HTTP requires a
separately deployed Waggle HTTP service.

`Memory` exposes async `prime`, `recall`, and `observe`. Prime calls
`prime_context`; recall calls `query_graph` with hybrid retrieval, ten nodes and
one relationship level. Observe calls `observe_conversation`. The graph invokes
these automatically when enabled. Observations contain workflow status, role,
opaque task/handoff identifiers and an optional curated `memory_note` from a
trusted executor (up to 2,000 characters). They exclude source bodies and raw
events. Recall can retrieve earlier decisions/results from any role or task in
the same project, while excluding other projects. Historical context is evidence,
not authority or executable instructions; current repository and workflow state
take priority.

Observation is an explicit opt-in to retain curated durable decisions/results,
not full Discord messages, provider responses, event payloads or transcripts.
The caller must select safe summaries; regex filtering cannot recognize every
possible secret. Obvious credential patterns and the configured Waggle token are
blocked before transmission; each observation field is limited to 4,000 characters.
Waggle itself persists the supplied observation fields verbatim before optional
fact extraction. Do not put credentials or personal data into those summaries.

Tool errors/timeouts produce bounded generic warnings without exception text,
credentials or server payloads. Memory failures do not fabricate context or
persistence. `persisted` is true only when Waggle explicitly returns
`verbatim_stored: true`; extraction failures remain visible as warnings. Returned
context is bounded to 8,000 characters. SDK lifecycle and transport negotiation
are handled by `mcp>=1,<2`, not handwritten JSON-RPC.

The adapter was checked against the installed upstream Waggle checkout:
`src/waggle/tools/dispatcher.py` (tool schemas),
`src/waggle/entrypoints/server_only.py` (bundled stdio), and
`src/waggle/protocol/mcp/http.py` (Streamable HTTP).
Upstream: [Waggle-mcp](https://github.com/Abhigyan-Shekhar/Waggle-mcp).
Unit tests verify scoped tool dispatch, opt-out, error handling and credential
filtering, including a real SDK stdio subprocess roundtrip against an isolated
minimal MCP test server. They do not claim a live production Waggle connection or successful
production writes.
