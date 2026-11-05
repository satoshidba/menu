# Using Larder from AI agents (MCP)

Larder includes a [Model Context Protocol](https://modelcontextprotocol.io) server, so an agent (Claude Code, Claude
Desktop or any MCP client) can check the pantry, plan the week, handle who's out, draft recipes, work through receipts
and log meals.

It is a thin client of Larder's own API. Every call uses **your agent's Larder API token**, so:
- the token's scopes decide what the agent can do;
- the same rules as the app apply: drafts before saving, duplicate and negative-stock protection, pet food kept
  separate, unknown nutrition never shown as zero;
- everything appears in Larder's audit trail under that token;
- revoking the token cuts the agent off immediately.

## 1. Create a token

In Larder, go to **Settings → Connected apps → AI agent**. This fills in the right scopes:

- `recipes:read|write`, `pantry:read|write`, `plan:read|write`, `receipts:write`
- `intake:read|write` for the adults
- `health:read|write` for medicines and vitamins

Add `intake:read:children` only if you want the agent to see the toddlers' intake. Copy the token (`lrd_…`); it's
shown once. You can rotate or revoke it on the same screen.

## 2. Connect an agent

### Remote (recommended): `https://<your site>/mcp`

The Docker stack runs the server as the `mcp` service, and Caddy serves it at `/mcp` over HTTPS. Clients authenticate
with `Authorization: Bearer <token>`; requests without a token get `401`.

**Claude Code:**

```bash
claude mcp add --transport http larder https://larder.home.arpa/mcp --header "Authorization: Bearer lrd_xxxxxxxx_xxxxxxxx"
```

On a home network using Caddy's private CA, the computer running the agent must trust that root certificate (see
[SETUP.md](SETUP.md#https-on-phones-and-tablets-home-network)). For Node-based clients such as Claude Code, you can
instead point at the exported root:

```bash
export NODE_EXTRA_CA_CERTS=$HOME/larder-root.crt
```

Any client that can send a custom `Authorization` header over streamable HTTP works the same way. Clients that only
support OAuth sign-in can't use the remote endpoint yet; use the local option below for those.

### Local (stdio): run the server on your computer

Install it from the repository:

```bash
pip install ./mcp-server
```

or, with uv, run it directly:

```bash
uvx --from ./mcp-server larder-mcp
```

It needs three environment variables:

| Variable | Example | |
|---|---|---|
| `LARDER_URL` | `https://larder.home.arpa` | Your Larder address |
| `LARDER_TOKEN` | `lrd_…` | The agent token |
| `LARDER_CA_CERT` | `/Users/you/larder-root.crt` | Only for Caddy's private CA |

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "larder": {
      "command": "uvx",
      "args": ["--from", "/path/to/menu/mcp-server", "larder-mcp"],
      "env": {
        "LARDER_URL": "https://larder.home.arpa",
        "LARDER_TOKEN": "lrd_xxxxxxxx_xxxxxxxx",
        "LARDER_CA_CERT": "/path/to/larder-root.crt"
      }
    }
  }
}
```

Claude Code (stdio):

```bash
claude mcp add larder --env LARDER_URL=https://larder.home.arpa --env LARDER_TOKEN=lrd_… -- uvx --from /path/to/menu/mcp-server larder-mcp
```

## What the agent can do

Read tools are annotated read-only. Removal tools are annotated destructive. Anything that saves or applies a draft
says so in its description, and the server's instructions tell agents to get your go-ahead first. `apply_receipt`
refuses to run without `confirm=true`.

Items, recipes and people can be named ("spinach", "Banana porridge", "Sam"); ids come back from the read tools.

| Area | Tools |
|---|---|
| Overview | `get_today` (next meal, use-soon, low stock, who's out, ideas) |
| Recipes | `find_recipes` (search or rank by what's in stock), `get_recipe` (scaled, with stock status and "check before serving"), `get_recipe_nutrition` |
| Recipe drafts | `import_recipe` (URL or text → draft), `suggest_recipe` (AI idea from stock → draft), `save_recipe`, `propose_recipe_change` (draft version + diff), `accept_recipe_change`, `discard_recipe_change` |
| Pantry | `get_pantry`, `get_use_soon`, `lookup_barcode`, `search_foods`, `add_to_pantry`, `use_from_pantry` (earliest dates first, never below zero), `adjust_stock` (move, correct count, mark opened), `get_price_history` |
| Plan and who's out | `get_week_plan` (meals + absences + conflicts), `plan_meal` (defaults to who's home), `update_planned_meal`, `remove_planned_meal`, `mark_away`, `remove_away`, `plan_without_absent` |
| Shopping | `get_shopping_list` (with reasons), `add_to_shopping_list`, `update_shopping_item` (tick off, change, snooze, dismiss) |
| Eating | `list_people`, `log_eaten`, `record_cooked_meal` (actual portions per person, leftovers, waste, pantry deductions), `get_intake` |
| Receipts | `import_receipt_text`, `list_receipts`, `get_receipt`, `match_receipt_line`, `apply_receipt` |
| Medicines and vitamins | `get_medicine_cabinet`, `get_doses_due`, `log_dose` (checks against the entered directions; conflicts return warnings and need the user's explicit confirmation), `list_doses`, `add_cabinet_item`, `add_routine` (needs `health:read`/`health:write`) |

**Resources:**
- `larder://today`
- `larder://pantry`
- `larder://shopping-list`
- `larder://week-plan`

**Prompts:**
- `plan_week`: plan dinners around stock, preferences and who's out, then show the shopping list
- `use_it_up`: meals for food close to its date
- `after_the_shop`: turn a pasted e-receipt into reviewed stock

Things you might ask:
- "Sam's at the pub Friday and I've got an evening class Tuesday. Plan dinners for the week using what needs eating
  first, keep it mild for the kids, and show me the shopping list."
- "We used the last of the spinach and about 300 g of pasta tonight."
- "Here's my Sainsbury's e-receipt: match what you can, show me the rest, and add it once I say so."
- "Mia's had her vitamin drops and Sam took 5 ml of the paracetamol at 7pm." (The agent records exactly that, and
  relays any warning, such as "too soon", before asking you.)
- "Make the dal recipe toddler-friendly, show me the differences, and save it as a separate version if I like it."

## Limits and safety

- **Scope is the limit.** Without `receipts:write` the agent can't touch receipts. Without `intake:read:children` it
  can't see the toddlers' intake (it still sees their profiles for planning if it has `plan:read`). Tokens never get
  admin powers: they can't manage users, other tokens, backups or exports.
- **Prompt injection.** Recipe pages and receipts are treated as data by Larder's own AI prompts, and the MCP server's
  instructions tell agents not to assert allergen safety or give medical, weight or feeding advice. But an agent
  reading a web page could still be influenced by it, so keep the review step: Larder never saves imports or applies
  receipts without an explicit call, and you can see and revert everything (versions are kept, and stock changes are
  ledger entries you can correct).
- **Costs.** `import_recipe` for unstructured pages, `suggest_recipe` and described `propose_recipe_change` use the
  server's AI provider (if configured). Their usage and estimated cost appear in **Settings → Server**.
- **Rate limits.** The API's import rate limit applies to agents too.

## Developing and testing

```bash
cd mcp-server && pip install -e . pytest anyio
```

The end-to-end tests drive a real MCP client against a running, seeded Larder:

```bash
LARDER_URL=http://127.0.0.1:8000 LARDER_TOKEN=lrd_… pytest
```

To run the HTTP transport locally:

```bash
LARDER_API_URL=http://127.0.0.1:8000 larder-mcp --transport http --port 8001
```

Set `LARDER_MCP_ALLOWED_HOSTS=larder.home.arpa` to turn on the SDK's DNS-rebinding protection when the server is
reachable without Caddy in front of it.
