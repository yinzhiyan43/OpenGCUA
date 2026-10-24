# OpenGCUA MCP for Codex

[中文安装、连接、重连与故障排查](CONNECT.zh-CN.md)

The macOS app includes the MCP server, Python runtime, SDK and signed driver.
The calling harness owns the overall task and next-step decisions. OpenGCUA uses
its configured model for a bounded UI operation: understand the interface, locate
targets, perform the requested operation and report the result. It reuses the app's
model settings and credentials; no separate Python installation is needed.

## Model-driven operations

Use `perform_operation(instruction, expected_result, constraints, request_id)` for
one small operation, not the whole open-ended task. For example: locate a specified
contact and open the chat, with explicit constraints forbidding typing or sending.
Poll `operation_status(operation_id)` for progress and results; use `stop_operation`
to cancel. `release` releases diagnostic sessions and does not stop a model operation.

The shared worker runs the existing native agent runtime, with default limits of
6 model steps and 180 seconds (maximum 12 steps / 300 seconds). The model is instructed
to stop at the expected result; these semantic constraints are model instructions,
not a separate filesystem or application sandbox. A request for clarification or
foreground approval stops the operation with `blocked` and returns the decision to
the calling harness. Nothing automatically answers or approves on the user's behalf.
Set `foreground_allowed=true` only when the user's authorization covers foreground
control for this operation; it does not broaden the operation's instruction/constraints.

Each operation reloads the desktop app's current Preferences and configuration file.
Settings stores credentials in an app-owned Keychain namespace, trusting only the
creating application, and verifies a noninteractive persistent read before success.
Tasks only read this namespace with Keychain interaction disabled; they never open
an authorization dialog or fall back to the legacy item. App restarts read the
persistent credential; MCP reconnects reuse the same host. The in-memory cache is
only an optimization. Settings imports legacy credentials once (macOS may require
one user authorization). Empty values retain a migration tombstone so clearing a
key cannot resurrect an old secret. Legacy items are preserved for recovery.
A locked Keychain or changed signing identity fails promptly with a Settings repair
message. External Keychain edits require reopening the app. Refused or missing
reads are not cached; explicitly unauthenticated configurations skip Keychain.
On macOS the file-based Keychain requires a scoped, serialized
`SecKeychainSetUserInteractionAllowed(false)` call; LAContext no-UI options do not
suppress its legacy authorization dialogs. No global lock policy is changed.
A private inherited pipe carries credentials from the signed native
host; credentials are not put in command arguments, environment variables or MCP
responses. Results include the selected model, model-request count, progress and
the model's reported answer. Verify its evidence before claiming success.

An identical `request_id` and specification returns the same operation within a host
lifetime. A changed specification with that ID is rejected. Operations continue
through a client disconnect, bounded by their deadline, and can be queried from a
new connection. Host shutdown cancels them; after a host restart, an unknown ID must
not be blindly resubmitted because earlier actions may have executed. Completed
records are retained in memory (up to 256 per host); private trajectories use the
app's normal run directory. A model operation exclusively owns the same desktop
lease as diagnostic tools. Status remains readable without opening another driver.

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
Registration writes configuration; it does not prove that an already-open chat
has loaded it. Check the MCP tool list in the client that hosts the chat.
An `unknown MCP server` response from that client means the server is absent
from that chat's registry, not that a handshake with this server failed.
Do not treat a standalone smoke test as proof that the current chat is connected.

> 使用 OpenGCUA MCP 查看权限和应用列表，然后读取当前窗口；先不要点击或输入，完成后释放会话。

## Shared application identity and permissions

The native `--mcp` entry point replaces itself with a lightweight stdio proxy.
All proxies connect to the **normal running OpenGCUA application**. If it is not
running, LaunchServices opens `/Applications/OpenGCUA.app` normally, without `-n`
or a separate `--mcp-host` instance. Its existing Python runtime serves MCP and
desktop commands through the same `NativeHost`. Model configuration is requested
from the running UI over its private pipe; operation events appear in the original
UI, progress panel and history. Diagnostic calls do not contact a model. Embedded
drivers retain the application's Accessibility and Screen Recording identity.

Proxy startup is serialized with a private file lock. An application lock prevents
duplicate UI processes, and a runtime lock prevents duplicate service workers.
The per-user directory is `/private/tmp/opengcua-service-<uid>` (0700); its socket
and locks are 0600. Symlinks, unexpected owners/types/modes and foreign socket
peers are rejected. A lock-owning host can replace a stale socket after a crash.

Each socket connection has its own MCP server session and view tokens. A shared
device lease permits only one connection to start/use a driver. Other connections
can still discover tools, but desktop calls return `RESOURCE_BUSY` until the owner
releases, disconnects, or reaches the 120-second device idle timeout. A non-owner's
release cannot close the active owner's driver. Desktop tasks use the same lease.
The runtime lives until the app exits; disconnecting MCP clients does not close
the original app or other clients.

The normal application owns the only Dock icon, including when its UI is open.
Stdio proxies and the
installation helper remain hidden. Quitting the host from the Dock gracefully
stops its worker and releases the device. Existing connections must reconnect to load an
updated native entry point. Status includes `host_pid` and `server_pid` so clients
can verify that they share the same service.

The stdio proxy remains alive if the shared host quits or disconnects. An
in-flight request receives `CONNECTION_LOST` with an explicit uncertain-outcome
warning; it is never replayed. The next request reconnects and repeats only the
MCP initialization handshake. Old view tokens cannot be used in the replacement
session. An already-closed client transport from an older installed proxy still
requires a client-side MCP restart; a successful standalone connection does not
repair or verify that chat's connection.

`status.host.dock` is an actual accessibility observation of OpenGCUA's Dock
items, made inside the signed native host using its existing Accessibility grant.
It includes count, title, application URL, and running state. It does not capture
the desktop or list other Dock applications. Permission errors, stale reports,
and unavailable Dock trees produce an unavailable/null count, never a guessed
count from process visibility. The private report is checked for ownership,
permissions, freshness, and matching host PID. `status` is passive: it reads the native host snapshot and the driver bundle's
Info.plist, without launching a driver, invoking `doctor`, acquiring desktop
control, extending an idle lease, or creating an SDK session. The version is
package metadata, not a fresh executable/signature verification. Permissions that
are not present in the host snapshot are `null`, never guessed as granted. A
running device session is preserved. Actual desktop operations still perform
signature/version validation and permission checks when starting the driver.

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

Version 0.3 uses the official low-level MCP server with explicit JSON Schemas.
`initialize`, `ping`, `tools/list`, and an unused `release` do not import the native
desktop SDK, start a driver, or access macOS permissions. The 11 existing diagnostic tool
names and arguments remain compatible, alongside three model-operation tools; unknown arguments are rejected before
desktop access. Native SDK failures become MCP `isError` results without breaking
tool discovery. Images remain native MCP image content blocks.

Each connection owns a lazy `DesktopTools` instance. The shared host grants only
one instance the desktop lease at a time. Its bridge serializes device operations,
expires idle sessions, and closes on release or disconnect (including cancellation). A released connection can start a new device session.
`catalog.py` defines the protocol contract, `server.py` owns the transport and
lifecycle, `operations.py` dispatches desktop operations, and `backend.py` owns the
SDK and private driver. The signed app/LaunchServices permission chain is unchanged.

| Tools | Purpose |
| --- | --- |
| `perform_operation` | Start a bounded UI operation using the app's configured model |
| `operation_status`, `stop_operation` | Read progress/results or cancel the operation |
| `status` | Passive package versions, available permission snapshots and session state; no control-component startup |
| `list_apps`, `list_windows` | Discover actual application/window identities |
| `observe` | Accessibility text, optional screenshots and a new `view_id` |
| `click`, `set_text`, `type_text`, `press_key` | Input grounded in the latest view |
| `click_image`, `scroll` | Screenshot-relative pixel input; scroll in lines |
| `release` | Close this connection's SDK session and private driver |

`observe` does not launch apps. Have the target app open first. For multiple
windows, pass a `window_id` returned by `list_windows`. Screenshots are MCP image
blocks with dimensions and image IDs. Browser apps use native accessibility;
this adapter does not expose Playwright APIs.

`list_apps` returns JSON with `apps`, `complete`, and guidance. Pass an observed
`apps[].id` as `app` to `list_windows` and `observe`. The macOS inventory contains
running processes and is intentionally incomplete; the SDK rejects name matching
in that case with `APP_INVENTORY_INCOMPLETE`. Refreshing does not make it complete.
Exact instance IDs remain usable and distinguish multiple instances with the same
name or bundle ID. Bundle IDs also work when they match only one running instance.

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

Multiline `type_text` normalizes CRLF/CR to LF and inserts the whole value with
one guarded paste. Its temporary clipboard item contains plain text plus escaped
HTML with `white-space: pre-wrap`, so rich editors preserve literal newlines
instead of creating paragraphs. Input is escaped as text, never executable HTML.
All original clipboard items and formats are kept in memory and restored after
dispatch, including on failure; a newer clipboard copy is never overwritten.
If any original format cannot be preserved, the tool refuses before pasting.
Single-line typing does not touch the clipboard. Use `press_key(key="enter")`
when a paragraph break is intended. A failed paste is never replayed automatically.

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
# Test repository source using the bundled dependencies (from repository root):
PYTHONPATH=packages/mcp "$PY" -B -m unittest discover -s packages/mcp/tests -v
PYTHONPATH=packages/mcp "$PY" -B packages/mcp/tests/smoke_stdio.py
# Separately test the installed app, after rebuilding/installing it:
"$PY" -I -B packages/mcp/tests/smoke_stdio.py --bundled-app /Applications/OpenGCUA.app
"$PY" -I -B packages/mcp/tests/smoke_stdio.py --bundled-app /Applications/OpenGCUA.app \
  --live --app app.generalist.mac --screenshot
```

The live smoke test reads permissions, app inventory and an already-open window.
It rejects invalid view tokens without dispatching input, then releases the
session. No model API calls are made. `tests/live_fixture.py` is an opt-in input
test for the repository-owned disposable fixture only.

`test_protocol.py` exercises real MCP requests over memory streams, including
discovery with unavailable native dependencies, invalid parameters, images,
connection isolation, cancellation and disconnect cleanup. It does not interact
with the desktop. Launcher socket tests require permission to bind a private
local Unix socket; the source smoke test only checks stdio and stale-view refusal.

For an opt-in Chrome regression, open `tests/browser_editors.html` in a separate
window, then run `tests/live_browser.py --bundled-app /Applications/OpenGCUA.app`.
It requires the dedicated fixture marker, checks full selections and exact values
(including leading/trailing/blank lines and CRLF), verifies the sibling editor
stays unchanged, closes the test window, and releases MCP even on failure.
`tests/live_textedit.py` creates its own temporary plain-text document in TextEdit,
tests multiline replacement, checks the saved file byte-for-byte as UTF-8 text,
then closes the document and releases MCP.

`tests/live_background.py` opens concurrent installed-app MCP connections, checks
that they share exactly one native host and worker with one Dock-visible host, verifies
device ownership and one driver, then checks idle shutdown after disconnect.
`tests/live_reconnect.py` checks actual Dock items, quits the host, and verifies
that the same four stdio clients reconnect to one replacement host without
desktop input. It also checks status during an active SDK session and saves
process/Dock observations to `/private/tmp/opengcua-reconnect-verification.json`.

For adapter development, `python -m opengcua_mcp` still supports standalone stdio
operation from an environment containing the local SDK and adapter. That mode
uses CuaDriver's own grants rather than the bundled host's grants. The installer
always registers the integrated app entry point.
