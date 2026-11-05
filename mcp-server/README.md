# larder-mcp

MCP server for [Larder](../README.md). Setup, tools and safety notes: [docs/MCP.md](../docs/MCP.md).

```bash
LARDER_URL=https://larder.home.arpa LARDER_TOKEN=lrd_... larder-mcp            # stdio
LARDER_API_URL=http://web:8000 larder-mcp --transport http --port 8001       # remote (behind Caddy at /mcp)
```
