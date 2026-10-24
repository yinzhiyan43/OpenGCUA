# OpenGCUA MCP 连接说明

适用于 Apple Silicon、macOS 14 及以上的 OpenGCUA 安装版。应用已内置 MCP、Python、SDK 和签名驱动。当前聊天的 harness 负责整体任务和下一步决策；OpenGCUA 调用自己已配置的模型完成有明确边界的界面操作，再返回结果。底层查看、点击工具保留作诊断。

## 模型操作如何工作

先在 OpenGCUA 的模型设置中配置服务地址、模型名和密钥（确实不需要认证的服务才开启免密选项）。MCP 每次开始操作都向正在运行的应用读取当前配置，使用桌面端同一个执行器。密钥在设置中保存为仅信任当前应用的持久钥匙串条目，并立即验证无需弹窗即可读取。任务执行只读取该条目，不弹系统授权框；应用重启后从持久存储读取，MCP 重连仍复用原应用。内存缓存只是减少读取次数。在钥匙串访问中从外部修改密钥后，应重新打开 OpenGCUA。JSON 配置仍在每次任务前重新读取。进度、结果与历史记录显示在原应用中。

1. 当前聊天调用 `perform_operation`，提供操作指令 `instruction`、预期结果 `expected_result`、限制列表 `constraints` 和唯一 `request_id`。
2. OpenGCUA 的模型理解界面、定位目标并完成这一小段操作。默认最多 6 步、180 秒；可设为最多 12 步、300 秒。
3. 当前聊天用 `operation_status` 查询进度、实际模型名、模型请求次数及结果，再决定下一步。`stop_operation` 可停止操作；`release` 只释放底层诊断会话。

例如：“找到指定联系人并打开聊天”，预期“确认聊天标题”，限制“不得输入或发送消息”。不要将整个开放式任务交给执行层。遇到歧义、提问或需要前台授权时，操作会以 `blocked` 停止并返回问题，由当前聊天处理。完成状态表示执行模型报告完成，仍应核对其返回的证据。

限制文本交给模型遵守，并不构成独立的应用或文件系统沙箱；步数与时间上限由运行器控制。同一后台内，相同 `request_id` 和参数不会重复执行。后台重启后旧操作 ID 失效，此时不得盲目重新提交，因为先前操作可能已经发生。客户端断开不立即取消操作，它仍受时间上限限制。

只有用户授权涵盖本次操作的前台控制时，调用方才可设置 `foreground_allowed=true`。它只允许本次操作使用前台输入，不扩大操作范围或解除限制。

`status` 只读取应用已有快照和组件版本文件，不启动控制组件、不运行 `doctor`、不占用桌面控制权，也不触发模型请求。未检查的权限返回 `null`；版本来自安装包元数据，实际操作启动时仍验证签名、版本和权限。`list_apps`、`observe` 等真正读取桌面的调用才按需启动控制组件，但也不会请求模型。模型操作与诊断工具共用唯一桌面控制权和同一个 MCP 后台。

## 1. 安装

打开 DMG，将 `OpenGCUA.app` 拖入 `Applications`，安装到 `/Applications/OpenGCUA.app`。更新前结束正在执行的桌面任务，退出旧版应用及其 MCP 后台，再替换安装。完成后推出 DMG，从“应用程序”打开安装版。

在“系统设置 → 隐私与安全性”中，为 **OpenGCUA** 启用“辅助功能”和“屏幕录制”。按 macOS 提示重新打开应用。MCP 不会自动授予这些权限。

只从安装路径启动。不要从 DMG、下载目录或源码构建目录启动额外副本；同名构建副本可能造成重复入口和不同的权限记录。此本地构建使用开发签名，未进行 Apple 公证。

## 2. 连接 Codex

已安装 Codex CLI 时，在 OpenGCUA 的“设置 → MCP”点击“连接到 Codex”，或在终端执行：

```bash
/Applications/OpenGCUA.app/Contents/MacOS/OpenGCUA --install-codex-mcp
```

等效的 Codex CLI 注册命令：

```bash
codex mcp add opengcua -- /Applications/OpenGCUA.app/Contents/MacOS/OpenGCUA --mcp
codex mcp list
```

注册后，在 Codex 的 MCP 设置中重新加载连接；若当前聊天仍未加载工具，重启 Codex 后重新进入聊天。更新应用后也需要重新加载，才能替换旧的连接进程。

手动配置时，在 `~/.codex/config.toml` 中合并以下内容，已有同名节就修改它，不要重复添加或覆盖其他配置：

```toml
[mcp_servers.opengcua]
command = "/Applications/OpenGCUA.app/Contents/MacOS/OpenGCUA"
args = ["--mcp"]
enabled = true
startup_timeout_sec = 60
tool_timeout_sec = 120
```

其他支持 stdio 的 MCP 客户端也使用相同的 `command` 和 `args`。这是本地 stdio 连接，不需要填写 HTTP 地址、端口或 API Key。不要将客户端配置为直接启动 `--mcp-host` 或构建目录中的 Python。

Codex 官方配置文档：https://learn.chatgpt.com/docs/extend/mcp?surface=cli

## 3. 在当前聊天验证

向 Codex 发送：

> 通过 OpenGCUA MCP 调用 status、list_apps，再调用 status，检查共享后台和实际 Dock 图标数量，最后 release。不要点击、输入或发送消息。

检查结果：

- 工具能在**当前聊天**调用成功。仅有 `codex mcp list`、配置注册成功或独立脚本测试通过，都不能证明当前聊天已经连上。
- `status` 中的 `host_pid`、`server_pid` 标识共享宿主和 Python 工作进程；多个聊天连接应看到相同的一组 PID。
- `status.host.dock.status` 为 `ok`，`status.host.dock.count` 为 `1`，对应项目的 `url` 指向 `/Applications/OpenGCUA.app/`。这是对 Dock 的实际辅助功能读取，不是根据进程数推算。
- 还要检查图标外观。`items[].bounds` 给出 Dock 项目的位置和尺寸，但数量、边框均正常也不能替代截图检查；极小图标或退出动画残留不算验收通过。应用启动与退出保持普通应用的显示策略，不通过切换隐藏策略管理 Dock 图标。
- `release` 成功释放本连接的桌面控制会话；其他聊天之后可以获取控制权。

这里的“一个后台服务”就是原 OpenGCUA 应用和它唯一的 Python 运行进程。应用已经启动时直接复用；没有启动时正常启动 `/Applications/OpenGCUA.app`。不再单独启动 `--mcp-host` 应用实例。桌面界面可以保持打开，MCP 与桌面共用执行器、模型配置、任务状态和 Dock 图标。每个客户端仍有一个不显示图标的轻量 stdio 代理。应用级锁防止重复启动，桌面任务、模型操作和诊断共用桌面控制权。

在 Dock 退出应用后，新版代理会在下一次请求时启动原应用并重新连接；只恢复初始化，不会重放点击或输入。重连后 PID 可以变化，但同时只应有一组共享服务。客户端全部断开不会退出原应用，运行进程随应用退出。底层诊断会话空闲 120 秒会释放控制权。

## 4. 常见问题

| 现象 | 处理方式 |
| --- | --- |
| `Transport closed` | 检查配置是否指向安装版，更新应用并重新加载客户端 MCP 连接。旧版已关闭的 stdio 管道无法靠独立脚本测试修复，必须在当前客户端重新建立连接，再调用 `status`。 |
| `CONNECTION_LOST` | 后台连接中断；下一次请求会尝试重连。进行中的操作可能已经执行，先重新观察窗口，再决定是否需要输入，避免重复发送。 |
| 找不到 `opengcua` 或没有 MCP 工具 | 检查注册命令、`codex mcp list` 和当前聊天的工具列表，重新加载连接或重启 Codex。 |
| `RESOURCE_BUSY` | 模型操作或另一连接持有桌面控制权。模型操作可等待完成或调用 `stop_operation`；诊断会话可调用 `release` 或等待空闲超时。无需启动另一个后台。 |
| 钥匙串反复请求密码 | 安装新版后打开模型设置，旧版凭据会在这一步迁移一次；macOS 如要求旧条目授权，需由用户在系统窗口完成。新条目只信任创建它的应用，不扩大到所有应用。保存时必须通过不弹窗的持久读取校验，随后重启应用、重连 MCP 均直接使用。若钥匙串被锁定或应用签名改变导致拒绝访问，任务立即返回设置修复提示，不反复弹窗。清空密钥保留空值标记，避免再次导入旧密钥；旧条目保留用于恢复，不再由任务读取。密钥不写入偏好设置或日志。 |
| 没有 `perform_operation` | 更新应用后，客户端还保留旧工具列表；在客户端重新加载 MCP，再检查工具发现结果。 |
| Dock 为 `permission_denied` | 在系统设置启用安装版 OpenGCUA 的辅助功能权限，按系统提示重开应用和连接。 |
| Dock 为 `unavailable` 或 `count: null` | 没有获得有效的 Dock 读数，不能认定为零个图标；稍后再次调用 `status`，检查权限和宿主是否刚重启。 |
| Dock 有多个同名图标 | 检查返回的 `items` 中应用路径和运行状态，更新到统一应用运行版，退出旧版 MCP 宿主或旧构建实例，移除旧副本的固定 Dock 项，再用 `status` 验证。不要反复启动更多副本。 |
| `APP_INVENTORY_INCOMPLETE` | 使用 `list_apps` 返回的精确 `apps[].id`，不要仅凭应用名匹配；之后依次 `list_windows`、`observe`。 |

卸载 MCP 连接但保留应用：

```bash
codex mcp remove opengcua
```

## 5. 源码验证

在仓库根目录，使用安装版自带的 Python 运行单元测试：

```bash
PY="/Applications/OpenGCUA.app/Contents/Resources/python/bin/python3"
PYTHONPATH=packages/mcp "$PY" -B -m unittest discover -s packages/mcp/tests -v
```

可选的实际重连回归测试（先结束正在进行的 MCP 桌面任务）：

```bash
"$PY" -I -B packages/mcp/tests/live_reconnect.py
```

该测试会建立四个客户端、退出并重启共享后台，检查连接恢复和实际 Dock 数量，不执行桌面输入。报告写入 `/private/tmp/opengcua-reconnect-verification.json`。它验证安装版行为；当前聊天仍需按第 3 节单独验证。
