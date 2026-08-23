# 0013 — Jira over REST v3, not MCP

Status: Accepted, implemented. Supersedes the Jira row of the tools table in
[architecture §5](../architecture.md#5-tools-layer).

## Decision

Reach Jira through its Cloud REST v3 API with a Python client, not through an MCP
server.

- **Auth**: HTTP basic, an Atlassian account email plus an API token.
  `TRIAGE_JIRA_BASE_URL`, `TRIAGE_JIRA_USER_EMAIL`, `TRIAGE_JIRA_API_TOKEN`.
- **Bodies**: Atlassian Document Format. Ticket bodies stay markdown internally
  and are translated at the boundary by `triage.integrations.adf`.
- **Endpoints used**: `POST /rest/api/3/issue`, `POST /rest/api/3/issue/{key}/comment`.

The architecture's rule — *MCP servers when they exist, Python tools otherwise* —
is unchanged. Jira simply falls on the second side of it. Datadog and GitHub
remain MCP.

## Why

No Jira MCP server is available in this environment. That is the whole reason;
there is no design argument for REST over MCP here.

Three consequences are worth recording, because they are not obvious.

**The client became testable.** The MCP version could not be meaningfully tested:
asserting against a mock of a protocol and a tool schema we do not control would
have proved only that the mock matched itself, which is why it carried a
"not exercised by the test suite" caveat. A REST request is fully determined by
our own code, so the payload shape, the auth header, the ADF translation and
Jira's error reporting are all pinned down in
`tests/integration/test_jira_client.py`. This is a net gain in confidence, not a
compromise.

**ADF is not optional.** REST v3 rejects a plain string where a description or
comment belongs, and the 400 it returns does not say which field was wrong.
Translating markdown at the boundary keeps ADF out of the graph, the prompts and
the Slack path, all of which still deal in markdown. The renderer covers the
subset the compose prompt is allowed to emit; anything outside it degrades to
literal text rather than being dropped, since a ticket that renders imperfectly
is recoverable and one missing a sentence is not.

**Jira's errors are worth unwrapping.** Field-level problems — a label with a
space, an issue type the project does not define — arrive in `errors` and
`errorMessages`, not in the status line. The client surfaces those as
`JiraError`, because the alternative is an operator reading a 400 with no field
named.

## Consequences

- The `mcp` dependency, and the vendored `httpx2` it pulled in, are removed.
  `httpx` is now a direct dependency.
- `TicketDraft.summary` gained `max_length=255`, Jira's own limit. Failing at
  compose names the field; failing at `POST /issue` does not.
- Deployment: `triage-ingress` and the Platform need egress to the Jira Cloud
  host. The Kubernetes Secret holds the email and token as a pair.
- Data Center / Server is **not** supported by this client: it uses REST v2 with
  bearer PATs and wiki-markup bodies. Supporting it would mean a second body
  renderer and a second auth mode.

## Revisit when

A Jira MCP server becomes available and is deployable here. Moving back would be
contained — `JiraClient` is a protocol with two implementations already — but it
would trade the test coverage above for whatever the server offers, so it needs a
reason beyond consistency.
