#!/bin/bash
# Install an independent runtime and register it without changing other Codex settings.
set -euo pipefail
repo="$(cd "$(dirname "$0")/../.." && pwd)"
mcp_home="${OPENGCUA_MCP_HOME:-$HOME/.local/share/opengcua-mcp}"
if [[ "$(uname -s)" != Darwin ]]; then
  echo "This MCP adapter currently requires macOS." >&2
  exit 1
fi
command -v uv >/dev/null
command -v codex >/dev/null
mkdir -p "$mcp_home"
if [[ ! -x "$mcp_home/.venv/bin/python" ]]; then
  uv venv --python 3.12 "$mcp_home/.venv"
fi
uv pip install --python "$mcp_home/.venv/bin/python" "$repo/packages/sdk" "$repo/packages/mcp"
"$mcp_home/.venv/bin/python" "$repo/packages/mcp/tests/smoke_stdio.py"
codex mcp add opengcua -- "$mcp_home/.venv/bin/python" -m opengcua_mcp
codex mcp get opengcua
echo "OpenGCUA MCP registered. Reopen the Codex chat/client if its tool catalog has not refreshed."
