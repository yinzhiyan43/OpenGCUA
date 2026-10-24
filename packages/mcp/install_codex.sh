#!/bin/bash
# Register the self-contained app; no external Python installation is needed.
set -euo pipefail
app="${OPENGCUA_APP:-/Applications/OpenGCUA.app}"
if [[ "$(uname -s)" != Darwin ]]; then
  echo "This MCP adapter currently requires macOS." >&2
  exit 1
fi
if [[ ! -f "$app/Contents/Resources/python/lib/python3.12/site-packages/opengcua_mcp/launcher.py" ]]; then
  echo "Install an OpenGCUA.app build that includes MCP first." >&2
  exit 1
fi
exec "$app/Contents/MacOS/OpenGCUA" --install-codex-mcp
