# TDMConsole

DevilXD 的 [TwitchDropsMiner](https://github.com/DevilXD/TwitchDropsMiner)（简称 TDM）是一个 tkinter GUI 程序。
本仓库把它改造成**交互式终端程序**，提供四种可随时切换的界面模式，
并**与上游保持极简同步**——原项目作为 git submodule 原样引入，一个文件都不改。

## 四种界面模式

| 模式 | 说明 | 适用 |
|---|---|---|
| **`tui`** | 全屏仪表盘（类 btop），实时面板 + 表格 + 快捷键 | 默认，交互终端 |
| **`repl`** | 指令式 REPL（类 Claude Code / Codex），滚动输出 + 底部 `/` 命令 | 喜欢命令驱动 |
| **`web`** | 浏览器仪表盘（Twitch 配色的拟物风 UI），HTTP + WebSocket | **Docker / 服务器**，多设备访问 |
| **`headless`** | 纯日志行，无交互 | 管道 / cron（无 TTY 时自动） |

模式偏好持久化在 `tdm-cli.json`，可随时切换：
- **TUI 模式**：按 `s` 打开设置，改「Interface mode」，Esc 保存即**运行中热切换**（挖矿不中断）。
- **REPL 模式**：输入 `/switch-mode tui`（或 `repl` / `web` / `headless`）即热切换。
- **Web 模式**：见下方 [Docker](#docker) 章节。
- 启动时：`--mode tui|repl|web|headless` 覆盖偏好；终端模式在无 TTY 时降级为 headless（web 不降级）。

```
# TUI 模式
┌ ⛏ TDMConsole │ Watching... │ user 12345678 │ ws 2 (70 topics) ─────────┐
│ ╭─ Mining ─────────────────────────────────────────────────────────╮   │
│ │ Game      Rust             Channel   shroud                      │   │
│ │ Drop      Rust Skin  ███████████████░░░░░░░░░  62.5%  ⏱ 0:42:10   │   │
│ │ Campaign  Rust Drops ███████░░░░░░░░░░░░░░░░░  30.0%  ⏱ 12:03:00  │   │
│ ╰──────────────────────────────────────────────────────────────────╯   │
│ ╭─ Channels ── Enter: pin & switch ──╮ ╭─ Campaigns ───────────────╮   │
│ │ ▶ shroud    Rust   ONLINE  42311 ✓ │ │ Rust      Rust Drops 3/10 │   │
│ ╰────────────────────────────────────╯ ╰───────────────────────────╯   │
│ ╭─ Log ────────────────────────────────────────────────────────────╮   │
│ │ 12:00:03 Claimed drop: Rust Skin                                 │   │
│ ╰──────────────────────────────────────────────────────────────────╯   │
└ q Quit │ r Reload │ g Games │ s Settings ──────────────────────────────┘

# REPL 模式
╭─── TDMConsole v16 ─────────────────────────────────────────────╮
│  ⛏ TDMConsole       Getting started                            │
│                       /help          list all commands         │
│  Welcome!             /status        miner status & progress   │
│  Twitch Drops Miner   /pin <channel> pin & switch to a channel │
│                       /switch-mode tui  full-screen dashboard  │
╰────────────────────────────────────────────────────────────────╯
12:00:03 Claimed drop: Rust Skin
12:00:05 [watching] shroud (Rust)
❯ /status
Status   : Watching shroud
Drop     : Rust Skin  62.5%  ⏱ 0:42:10
────────────────────────────────────────────────────────────────
❯ _
────────────────────────────────────────────────────────────────
⛏ Watching shroud · Rust Skin 62.5%          ● repl · /switch-mode tui
```

## 登录

登录**不会自动弹框**。首次需要登录时，界面只在日志/状态里提示「login required」，
你**主动触发**才显示设备码：

- **TUI**：按 `l`（或用鼠标点底部的 `Login`），弹出居中模态框显示 URL + 设备码。
- **REPL**：输入 `/login`，弹出同样的模态框。
- **headless**：无法交互，直接打印设备码方框（无头场景的唯一方式）。

在任意设备浏览器打开 URL、输入代码授权后，后台自动完成登录并关闭提示，无需按键。
模态框可按 `Esc` 隐藏（后台仍在等待授权），设备码过期会自动轮换。

## 工作原理（为什么能零改动同步上游）

TDM 的核心逻辑（`twitch.py` / `channel.py` / `websocket.py` / `inventory.py`）
对 GUI 的**唯一运行时耦合**只有一行：`twitch.py` 里的 `from gui import GUIManager`。
其余 `from gui import ...` 全部位于 `TYPE_CHECKING` 块下，运行时不执行。

于是本项目的做法是：在 `import twitch` 之前，把一个**终端版 `GUIManager`**注入
`sys.modules["gui"]`，核心逻辑就会用上 CLI 界面，而 submodule 里的代码保持 100% 原样。

```
main.py
  └─ tdm_cli/bootstrap.py    submodule 加入 sys.path、stub GUI 依赖、注入 gui、路径覆盖
     └─ tdm_cli/gui.py       CLI 版 GUIManager（接口与上游一致），更新 state + 调用前端
        ├─ tdm_cli/state.py            共享状态（前端无关）
        ├─ HeadlessFrontend            纯日志行（服务器/管道/--no-tui）
        └─ tdm_cli/tui/  TextualFrontend + 全屏仪表盘（交互终端默认）
```

- **零改动 submodule** → 上游更新永不产生合并冲突。
- 唯一契约是 `GUIManager` 的公开接口；上游若改动它，`--check-contract` 会指出缺了什么，只需改 `tdm_cli/gui.py`。

## 安装

```bash
git clone --recursive <this-repo-url> TDM-CLI
cd TDM-CLI
# 或者：已克隆但没拉 submodule
git submodule update --init --recursive
```

本项目用 [uv](https://docs.astral.sh/uv/) 管理与运行 Python（3.10+）。首次运行自动创建虚拟环境并装依赖（`aiohttp`、`truststore`、`textual`、`prompt_toolkit`——GUI 专属的 Pillow/pystray/tkinter 已被 stub，**无头机器无需安装**）。

## 快速开始

```bash
uv run main.py init      # （可选）交互式向导：生成 settings.json + 选界面模式
./run.sh                 # = uv run main.py，按保存的模式启动（默认 TUI）
```

首次运行会弹出**设备码登录**（TUI 里是居中模态框，REPL / 无头模式打印方框）：
在任意设备浏览器打开 URL、输入代码授权后自动开始挖矿，无需按键。
凭据保存在 `cookies.jar`，之后不再需要登录。

## TUI 快捷键

| 键 | 作用 |
|---|---|
| `q` / Ctrl+C | 退出（优雅收尾并保存） |
| `l` | 登录（弹出设备码框；也可鼠标点底部 `Login`） |
| `r` | 重载（重新拉取库存/频道） |
| `g` | 游戏优先级 & 排除编辑器（Enter 添加/移除，`u`/`d` 排序，`x` 排除，Esc 保存返回） |
| `s` | 设置（**界面模式** / 代理 / 语言 / 优先模式 / 连接质量，Esc 保存返回） |
| Enter（频道表） | 置顶并切换到选中频道（📌 固定，直到取消） |
| Esc | 取消置顶，恢复自动选台 |

## REPL 斜杠命令

在 `repl` 模式下（`/switch-mode repl` 或 `--mode repl`），底部提示符输入命令（支持 Tab 补全）：

| 命令 | 作用 |
|---|---|
| `/help` | 列出所有命令 |
| `/status` | 当前状态、观看频道、掉落进度 |
| `/channels` / `/campaigns` | 列出频道 / 库存活动 |
| `/games` | 显示优先 & 排除游戏 |
| `/priority add\|remove\|up\|down <游戏>` | 编辑优先级列表 |
| `/exclude add\|remove <游戏>` | 编辑排除列表 |
| `/pin <频道>` / `/unpin` | 置顶切换频道 / 恢复自动 |
| `/proxy <url\|clear>` | 设置或清除代理（触发重载） |
| `/reload` | 重新拉取库存和频道 |
| `/priority-mode <模式>` | 设置优先模式（priority_only / ending_soonest / low_avbl_first） |
| `/switch-mode tui\|repl\|web\|headless` | 切换界面模式 |
| `/login` | 显示设备码登录框（仅在需要登录时） |
| `/quit` | 退出 |

## 命令行参数

```
uv run main.py [init] [选项]
```

| 参数 | 说明 |
|---|---|
| `init` | 交互式向导，生成设置文件后退出 |
| `-c, --config PATH` | 设置文件路径（默认 `./settings.json`） |
| `--mode tui\|repl\|web\|headless` | 界面模式（覆盖保存的偏好；默认读 `tdm-cli.json`，无则 `tui`） |
| `--host ADDR` | web 模式绑定地址（默认 `$TDM_WEB_HOST` 或 `127.0.0.1`；Docker 用 `0.0.0.0`） |
| `--port PORT` | web 模式端口（默认 `$TDM_WEB_PORT` 或 `8080`） |
| `--proxy URL` | 代理，例如 `http://127.0.0.1:7890`（会保存进设置文件） |
| `--games "A,B"` | 逗号分隔的优先游戏列表（覆盖设置文件中的 priority） |
| `--cookie TOKEN` | 直接注入 Twitch auth token，跳过设备码登录 |
| `--jar PATH` | cookies.jar 路径（默认 `./cookies.jar`，与原程序格式一致） |
| `--no-tui` | `--mode headless` 的别名（stdout 非终端时自动生效） |
| `-v` | 日志详细度（可叠加 `-vvvv`） |
| `--log` | 写日志文件 `log.txt` |
| `--check-contract` | 校验 CLI 界面层与 submodule 接口匹配后退出 |
| `--version` | 打印版本 |

环境变量：`TDM_WEB_HOST` / `TDM_WEB_PORT`（web 模式绑定），`TDM_DATA_DIR`（把所有运行时状态重定向到一个目录，Docker 用它挂卷持久化）。

无头示例（systemd / cron / nohup）：

```bash
uv run main.py --mode headless --proxy http://127.0.0.1:7890 --games "Rust,VALORANT"
```

## Docker

Web 模式专为容器设计：一个浏览器仪表盘（**Twitch 配色**的拟物风 UI——深色紫调、3D 立体质感、
HTTP + WebSocket 实时更新），无需 TTY，登录用设备码（在网页里点 **Log in**，或看 `docker logs`）。

```bash
# 1. 拉取 submodule（构建上下文需要）
git submodule update --init --recursive

# 2. 构建
docker build -t tdm-cli .

# 3. 运行（-v 挂卷持久化登录/设置到 /data）
docker run -d --name tdm -p 8080:8080 -v tdm-data:/data tdm-cli

# 4. 浏览器打开 http://localhost:8080 → 点 Log in 完成设备码授权
docker logs -f tdm      # 挖矿日志也会打到 stdout
```

- 镜像基于 `ghcr.io/astral-sh/uv`，用 `uv sync --frozen` 锁定依赖。
- `TDM_DATA_DIR=/data` 把 `settings.json` / `cookies.jar` / `tdm-cli.json` / `log.txt` / `cache/`
  全部落到挂载卷，容器重启后登录与配置不丢。
- 直接本地跑网页版（不进 Docker）：`uv run main.py --mode web --host 0.0.0.0 --port 8080`。

Web UI 提供与终端模式一致的能力：实时挖矿仪表盘（进度表/频道表/活动卡）、
点击频道置顶切换、Games/Settings 弹窗（改优先级/排除/代理/优先模式）、控制台日志、
设备码登录框（点 Log in 才显示，过期自动轮换）。

## 配置

- **游戏 / 代理 / 语言等**持久化在 `settings.json`（`init` 向导、TUI 的 `g`/`s` 屏幕、
  REPL 的 `/priority` `/proxy` 等命令、CLI 参数都会写它；也可直接手改——字段定义见
  submodule `TwitchDropsMiner/settings.py` 的 `SettingsFile`）。
- **界面模式**持久化在 `tdm-cli.json`（本项目独有，不进 submodule 的设置文件）。

运行时产生的文件（`settings.json` / `tdm-cli.json` / `cookies.jar` / `log.txt` / `lock.file`）
都在**外层仓库根目录**，不污染 submodule，且已被 `.gitignore` 忽略。

## 与上游同步更新

```bash
git submodule update --remote TwitchDropsMiner   # 拉上游最新 master
uv run main.py --check-contract                  # 自检接口是否漂移
```

- 输出 `OK` → 直接提交 submodule 指针即可。
- 报告 “Interface drift” → 上游改了 `GUIManager` 接口，按提示更新 `tdm_cli/gui.py` 对应方法（依然不用改 submodule）。

## 测试

```bash
uv run test_tui.py          # 无头驱动 TUI：仪表盘/登录模态/games/settings/置顶/退出
uv run test_repl.py         # REPL 命令 + 模式热切换（顺序/持久化/日志路由）
uv run test_web.py          # web 服务器：snapshot 序列化/HTTP/WS 命令往返/生命周期
uv run main.py --check-contract
```

## 目录结构

```
TDM-CLI/
├── TwitchDropsMiner/     # git submodule（DevilXD 上游，保持原样）
├── tdm_cli/
│   ├── bootstrap.py      # sys.path + stub 依赖 + 注入 gui + 路径覆盖
│   ├── gui.py            # CLI 版 GUIManager（核心，含 HeadlessFrontend + 模式热切换）
│   ├── state.py          # 前端无关的共享状态
│   ├── prefs.py          # 界面模式偏好持久化（tdm-cli.json）
│   ├── console.py        # TTY 感知输出
│   ├── commands.py       # UI 无关的斜杠命令处理器
│   ├── repl.py           # REPL 前端（Textual，Claude Code 风格）
│   ├── wizard.py         # init 配置向导
│   ├── tui/              # Textual 界面
│   │   ├── __init__.py   #   TextualFrontend（桥接 GUIManager ↔ App）
│   │   ├── app.py        #   MinerApp：全屏仪表盘
│   │   ├── repl_app.py   #   ReplApp：Claude Code 风格指令界面
│   │   └── screens.py    #   登录模态 / Games / Settings 屏幕
│   └── web/              # WebUI 前端（Docker）
│       ├── __init__.py   #   WebFrontend（启动 aiohttp 服务器）
│       ├── server.py     #   state snapshot + HTTP/WS + 广播
│       └── static/       #   拟物风 SPA：index.html / app.css / app.js
├── main.py               # 启动器 + CLI 参数 + 模式解析
├── Dockerfile            # web 模式容器镜像（uv 基镜像）
├── test_tui.py           # TUI 回归测试（无头）
├── test_repl.py          # REPL + 热切换回归测试（无头）
├── test_web.py           # web 服务器 HTTP/WS 回归测试
├── pyproject.toml        # uv 项目 + 依赖
└── run.sh
```

