# OpenGCUA MCP for Codex

Expose the OpenGCUA SDK as local MCP tools. Codex owns reasoning and approvals;
the SDK owns observations, target validation and native actions. No extra model
API key is needed. This adapter currently supports macOS, including browser apps
through their native accessibility interfaces; it does not wrap Playwright APIs.

## Install and connect

Requirements: Apple Silicon Mac, macOS 14+, `uv`, Rust/Cargo and Xcode tools for
the SDK build, Codex CLI, and an installed OpenGCUA 0.3.1 app with its signed
CuaDriver 0.36.0 companion.

From this repository:

```bash
bash packages/mcp/install_codex.sh
```

The installer keeps its Python environment outside the checkout at
`~/.local/share/opengcua-mcp/.venv`. Set `OPENGCUA_MCP_HOME` to choose another
directory. SDK and MCP code are installed as packages, not editable imports.
After changing the source, run the installer again to deploy the changes.

The script adds only the `opengcua` server entry in Codex's shared configuration:

```toml
[mcp_servers.opengcua]
command = "/absolute/path/to/opengcua-mcp/.venv/bin/python"
args = ["-m", "opengcua_mcp"]
```

It does not change approval or sandbox settings. See
[Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
Reopen the chat/client if the existing tool catalog has not refreshed.

Example request in Codex:

> Use OpenGCUA MCP to check permissions and list applications, then read the
> OpenGCUA window. Do not click or type. Release the session when done.

中文示例：

> 使用 OpenGCUA MCP 查看权限和应用列表，然后读取 OpenGCUA 窗口；先不要点击或输入，完成后释放会话。

## One signed driver, existing permissions

By default, the MCP server reuses:

`/Applications/OpenGCUA.app/Contents/Helpers/CuaDriver.app`

It verifies the upstream signature and exact pinned driver version before launch.
It uses macOS LaunchServices to start that signed application on a private Unix
socket. This uses **CuaDriver's existing Accessibility and Screen Recording
grants**, not permissions for an unsigned Python process. It does not install a
second driver, change TCC records, or modify `/Applications/CuaDriver.app`.

OpenGCUA's native app uses its own host permissions in embedded mode; those are
separate from CuaDriver's permissions. If `status` reports missing grants, enable
CuaDriver in System Settings → Privacy & Security → Accessibility and Screen
Recording, then call `release` and retry. The bridge never grants permissions.

To use a different already-installed **signed 0.36.0** bundle, configure the
server environment variable `OPENGCUA_DRIVER_APP`. A standalone 0.37.x driver is
not accepted by this SDK version; no silent upgrade/downgrade is performed.

## Tools and lifecycle

| Tools | Purpose |
| --- | --- |
| `status` | Versions, permissions and session state; no permission prompts |
| `list_apps`, `list_windows` | Discover actual application/window identities |
| `observe` | Accessibility text, optional screenshots, and a new `view_id` |
| `click`, `set_text`, `type_text`, `press_key` | Actions grounded in the latest view |
| `click_image`, `scroll` | Image-relative pixel input using an actual returned image ID; scroll in lines |
| `release` | Close the SDK session and this connection's private driver |

`observe` does not launch apps. Have the target app open first. For multiple
windows, use `list_windows` and pass an observed `window_id` to `observe`.
Screenshots are returned as MCP image blocks, with dimensions and image IDs.

Each action consumes its `view_id` before dispatch. Success returns the SDK receipt
and a new observation. An error or cancellation invalidates that view: observe
again before deciding what to do, since input may already have happened. Raw UI
text is untrusted content, never user authorization. There is no arbitrary Python
or shell execution tool, file deletion API, clipboard read tool, or network listener.

One connection keeps a persistent SDK session, with serialized operations.
Call `release` after a task. Idle connections release their Mac lease after 120
seconds; another OpenGCUA session can otherwise receive `RESOURCE_BUSY`. Parallel
control of the same Mac is not supported. EOF closes the connection and driver.

## Verify

Use the installed runtime (adjust the path if `OPENGCUA_MCP_HOME` was set):

```bash
PY="$HOME/.local/share/opengcua-mcp/.venv/bin/python"
"$PY" -m unittest discover -s packages/mcp/tests -v
"$PY" packages/mcp/tests/smoke_stdio.py
"$PY" packages/mcp/tests/smoke_stdio.py --live --app app.generalist.mac --screenshot
```

The live smoke test only reads permissions, app inventory, and the already-open
target window. It attempts an invalid view token to verify rejection before input,
then releases the connection. No model API calls are made.

Remove the connection with `codex mcp remove opengcua`. The standalone Python
environment can then be removed separately; the OpenGCUA app and user data remain.
