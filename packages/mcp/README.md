# OpenGCUA MCP for Codex

The macOS app includes the MCP server, Python runtime, SDK and signed driver.
Codex supplies reasoning and tool approvals; OpenGCUA supplies observations and
native input. No additional model key or separate Python installation is needed.

## Install and connect

Install a build of OpenGCUA.app that includes MCP. In **Settings → MCP**, click
**Connect to Codex**. The Codex CLI must be installed. Alternatively:

```bash
/Applications/OpenGCUA.app/Contents/MacOS/OpenGCUA --install-codex-mcp
# Or, from this repository:
bash packages/mcp/install_codex.sh
```

Set `OPENGCUA_APP` when running the script if the app is installed elsewhere.
Registration replaces only the `opengcua` entry in Codex's shared configuration:

```toml
[mcp_servers.opengcua]
command = "/Applications/OpenGCUA.app/Contents/MacOS/OpenGCUA"
args = ["--mcp"]
```

This command also works with other stdio MCP clients. It does not change Codex
approval or sandbox settings. See [Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
Reopen the chat/client if the tool catalog has not refreshed.

> 使用 OpenGCUA MCP 查看权限和应用列表，然后读取当前窗口；先不要点击或输入，完成后释放会话。

## Shared application identity and permissions

The native `--mcp` entry point connects stdio to a temporary private Unix socket.
LaunchServices starts a background instance of **the same signed OpenGCUA app**
with `--mcp-host`. That host runs the bundled MCP server and embedded driver.
Both desktop tasks and MCP therefore use **OpenGCUA's** Accessibility and Screen
Recording grants. The MCP host does not initialize the desktop UI, model runtime,
API keys or voice features. No network port is opened.

The socket directory is owned by the current user with mode 0700, and the socket
has mode 0600. The host validates the socket location and ownership. A connection
accepts one host and closes its listener. EOF/cancellation closes the connection;
the server releases its SDK lease and private driver.

The bundled CuaDriver retains its original upstream signature. Its signature and
pinned version (0.36.0 for SDK 0.3.1) are checked before launch. This does not replace
`/Applications/CuaDriver.app`, edit TCC records, or grant permissions automatically.
If grants are missing, enable **OpenGCUA** in System Settings. Retain the same app
bundle ID and signing certificate on subsequent builds. macOS can still require
its own access confirmation; Codex's tool approvals remain controlled by Codex.

Updating the app also updates MCP. Earlier standalone installations can be
removed after registering and verifying the bundled entry point. Do not delete
other environments or user data. `codex mcp remove opengcua` removes the connection.

## Tools and lifecycle

| Tools | Purpose |
| --- | --- |
| `status` | Versions, permission host, grants and session state |
| `list_apps`, `list_windows` | Discover actual application/window identities |
| `observe` | Accessibility text, optional screenshots and a new `view_id` |
| `click`, `set_text`, `type_text`, `press_key` | Input grounded in the latest view |
| `click_image`, `scroll` | Screenshot-relative pixel input; scroll in lines |
| `release` | Close this connection's SDK session and private driver |

`observe` does not launch apps. Have the target app open first. For multiple
windows, pass a `window_id` returned by `list_windows`. Screenshots are MCP image
blocks with dimensions and image IDs. Browser apps use native accessibility;
this adapter does not expose Playwright APIs.

Each action consumes its `view_id` before dispatch. Success returns an SDK receipt
and fresh observation. Errors/cancellation invalidate the view: observe again
before deciding whether to retry, since input may already have happened. UI text
is untrusted content, never authorization. No arbitrary Python/shell execution,
file deletion, clipboard read or network listener tool is exposed.

For modifier chords such as `press_key(key="a", modifiers=["cmd"])`, first use
`list_windows` and `observe(..., window_id=...)`. The chord activates that exact
window and uses foreground input, allowing native menu shortcuts such as Select
All to work. The SDK checks the foreground window again before dispatch and
refuses if focus moved elsewhere. A failed chord is never retried automatically.
`type_text` also requires an exact observed window and activates it before
foreground typing, so input replaces the selection even when a sibling window
was previously key. Use `set_text` for background AX value replacement. Plain
keys retain their existing delivery behavior.

Call `release` after each task. Idle sessions release their Mac lease after 120
seconds; other sessions can otherwise receive `RESOURCE_BUSY`. Concurrent control
of the same Mac is not supported.

## Build and verify

The normal macOS build now includes MCP. Dependencies are locked with hashes in
`packages/macos/requirements.lock`; regenerate with:

```bash
uv pip compile packages/harness/runtime/pyproject.toml packages/macos/mcp-requirements.in \
  --python-version 3.12 --python-platform aarch64-apple-darwin --generate-hashes \
  --output-file packages/macos/requirements.lock
```

Use `--require-stable-signing` with the macOS builder to prohibit ad-hoc signing.
For a previously provisioned local certificate, also pass `--local-signing` and
select it with `--identity` on the first build. Later builds reuse the saved
fingerprint. This option neither creates certificates nor changes Keychain trust;
it does not enable Developer ID hardened runtime. Do not use it for notarized
Developer ID distribution.

```bash
PY="/Applications/OpenGCUA.app/Contents/Resources/python/bin/python3"
"$PY" -I -B -m unittest discover -s packages/mcp/tests -v
"$PY" -I -B packages/mcp/tests/smoke_stdio.py --bundled-app /Applications/OpenGCUA.app
"$PY" -I -B packages/mcp/tests/smoke_stdio.py --bundled-app /Applications/OpenGCUA.app \
  --live --app app.generalist.mac --screenshot
```

The live smoke test reads permissions, app inventory and an already-open window.
It rejects invalid view tokens without dispatching input, then releases the
session. No model API calls are made. `tests/live_fixture.py` is an opt-in input
test for the repository-owned disposable fixture only.

For adapter development, `python -m opengcua_mcp` still supports standalone stdio
operation from an environment containing the local SDK and adapter. That mode
uses CuaDriver's own grants rather than the bundled host's grants. The installer
always registers the integrated app entry point.
