# Official TradingView MCP

Probed 2026-11-06 with `python3 scripts/tv-mcp-discover.py` against `https://mcp.tradingview.com/mcp`. `SIGMA_TV_MCP_TOKEN` was unset. The probe calls no tool other than `initialize`, and it stops when that call is rejected.

## Auth method

OAuth 2.0 only. `initialize` returned HTTP 401:

```json
{"detail":"This server requires OAuth authentication"}
```

`WWW-Authenticate`:

```text
Bearer resource_metadata="https://mcp.tradingview.com/.well-known/oauth-protected-resource/mcp"
```

Protected resource (`GET` that metadata URL):

| Field | Value |
|---|---|
| resource | `https://mcp.tradingview.com/mcp` |
| authorization_servers | `https://www.tradingview.com` |
| bearer_methods_supported | `header` |

Authorization server (`https://www.tradingview.com/.well-known/oauth-authorization-server`):

| Field | Value |
|---|---|
| authorization_endpoint | `https://www.tradingview.com/mcp/oauth/authorize` |
| token_endpoint | `https://www.tradingview.com/mcp/oauth/token` |
| registration_endpoint | `https://www.tradingview.com/mcp/oauth/register` |
| revocation_endpoint | `https://www.tradingview.com/mcp/oauth/revoke` |
| grant_types_supported | `authorization_code`, `refresh_token` |
| response_types_supported | `code` |
| code_challenge_methods_supported | `S256` |
| scopes_supported | `mcp:read`, `mcp:tools` |
| token_endpoint_auth_methods_supported | `none`, `client_secret_basic`, `client_secret_post` |

There is no static API key on this endpoint. A bearer token is only an OAuth access token from that authorization-code flow.

## serverInfo

none. `initialize` was rejected before a session existed.

## tools/list

none. The probe does not call `tools/list` after a 401.

## Verdict

```text
BACKTEST_TOOL: none
OHLC_TOOL: none
```

## Gate

Phase 2 stops here. Tasks 4 and 5 (standard-MCP transport, switching Sigma off tvremix) stay unstarted while both verdict lines are `none`. Sigma keeps its current TradingView connection until a person completes this OAuth flow, exports the access token as `SIGMA_TV_MCP_TOKEN` in the shell only, and re-runs the probe. The token is never written into a committed file. A later probe that prints real tool names is what unblocks the transport.
