# TDM-CLI

DevilXD 的 [TwitchDropsMiner](https://github.com/DevilXD/TwitchDropsMiner)（简称 TDM）是一个 tkinter GUI 程序。
本仓库把它改造成**交互式终端程序（TUI）**：在交互终端里是一个类似 btop 的全屏仪表盘，
在服务器 / Docker / 管道里自动退化为纯日志无头模式；
同时**与上游保持极简同步**——原项目作为 git submodule 原样引入，一个文件都不改。

```
┌ ⛏ TDM-CLI │ Watching... │ user 12345678 │ ws 2 (70 topics) ────────────┐
│ ╭─ Mining ─────────────────────────────────────────────────────────╮   │
│ │ Game      Rust             Channel   shroud                      │   │
│ │ Drop      Rust Skin                                              │   │
│ │           ███████████████░░░░░░░░░  62.5%  ⏱ 0:42:10             │   │
│ │ Campaign  Rust Drops (3/10 claimed)                              │   │
│ │           ███████░░░░░░░░░░░░░░░░░  30.0%  ⏱ 12:03:00            │   │
│ ╰──────────────────────────────────────────────────────────────────╯   │
│ ╭─ Channels ── Enter: pin & switch ──╮ ╭─ Campaigns ───────────────╮   │
│ │ ▶ shroud    Rust   ONLINE  42311 ✓ │ │ Rust      Rust Drops 3/10 │   │
│ │   summit1g  Rust   offline     - ✓ │ │ VALORANT  V Drops    0/5  │   │
│ ╰────────────────────────────────────╯ ╰───────────────────────────╯   │
│ ╭─ Log ────────────────────────────────────────────────────────────╮   │
│ │ 12:00:03 Claimed drop: Rust Skin                                 │   │
│ ╰──────────────────────────────────────────────────────────────────╯   │
└ q Quit │ r Reload │ g Games │ s Settings ──────────────────────────────┘
```

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

本项目用 [uv](https://docs.astral.sh/uv/) 管理与运行 Python（3.10+）。首次运行自动创建虚拟环境并装依赖（`aiohttp`、`truststore`、`textual`——GUI 专属的 Pillow/pystray/tkinter 已被 stub，**无头机器无需安装**）。

## 快速开始

```bash
uv run main.py init      # （可选）交互式向导生成 settings.json
./run.sh                 # = uv run main.py，交互终端里启动 TUI 仪表盘
```

首次运行会弹出**设备码登录**（TUI 里是居中模态框，无头模式打印方框）：
在任意设备浏览器打开 URL、输入代码授权后自动开始挖矿，无需按键。
凭据保存在 `cookies.jar`，之后不再需要登录。

## TUI 快捷键

| 键 | 作用 |
|---|---|
| `q` / Ctrl+C | 退出（优雅收尾并保存） |
| `r` | 重载（重新拉取库存/频道） |
| `g` | 游戏优先级 & 排除编辑器（Enter 添加/移除，`u`/`d` 排序，`x` 排除，Esc 保存返回） |
| `s` | 设置（代理 / 语言 / 优先模式 / 连接质量，Esc 保存返回） |
| Enter（频道表） | 置顶并切换到选中频道（📌 固定，直到取消） |
| Esc | 取消置顶，恢复自动选台 |

## 命令行参数

```
uv run main.py [init] [选项]
```

| 参数 | 说明 |
|---|---|
| `init` | 交互式向导，生成设置文件后退出 |
| `-c, --config PATH` | 设置文件路径（默认 `./settings.json`） |
| `--proxy URL` | 代理，例如 `http://127.0.0.1:7890`（会保存进设置文件） |
| `--games "A,B"` | 逗号分隔的优先游戏列表（覆盖设置文件中的 priority） |
| `--cookie TOKEN` | 直接注入 Twitch auth token，跳过设备码登录 |
| `--jar PATH` | cookies.jar 路径（默认 `./cookies.jar`，与原程序格式一致） |
| `--no-tui` | 禁用仪表盘，输出纯日志行（stdout 非终端时自动生效） |
| `-v` | 日志详细度（可叠加 `-vvvv`） |
| `--log` | 写日志文件 `log.txt` |
| `--check-contract` | 校验 CLI 界面层与 submodule 接口匹配后退出 |
| `--version` | 打印版本 |

无头示例（systemd / Docker / nohup）：

```bash
uv run main.py --no-tui --proxy http://127.0.0.1:7890 --games "Rust,VALORANT"
```

## 配置

设置持久化在 `settings.json`（`init` 向导、TUI 的 `g`/`s` 屏幕、CLI 参数都会写它；
也可以直接手改——字段定义见 submodule `TwitchDropsMiner/settings.py` 的 `SettingsFile`）。

运行时产生的文件（`settings.json` / `cookies.jar` / `log.txt` / `lock.file`）都在
**外层仓库根目录**，不污染 submodule，且已被 `.gitignore` 忽略。

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
uv run main.py --check-contract
```

## 目录结构

```
TDM-CLI/
├── TwitchDropsMiner/     # git submodule（DevilXD 上游，保持原样）
├── tdm_cli/
│   ├── bootstrap.py      # sys.path + stub 依赖 + 注入 gui + 路径覆盖
│   ├── gui.py            # CLI 版 GUIManager（核心，含 HeadlessFrontend）
│   ├── state.py          # 前端无关的共享状态
│   ├── console.py        # TTY 感知输出
│   ├── wizard.py         # init 配置向导
│   └── tui/              # Textual 全屏仪表盘（app / screens / frontend）
├── main.py               # 启动器 + CLI 参数
├── test_tui.py           # TUI 回归测试（无头）
├── pyproject.toml        # uv 项目 + 依赖
└── run.sh
```
