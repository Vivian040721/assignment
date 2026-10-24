# Paper Agent v0.2.2

Paper Agent 是本机运行的论文研究桌面应用。当前界面使用 **Electron + React + TypeScript + Vite**；Python 后端保留 MainAgent、MCP Client/Server、arXiv 与 OpenAlex 来源、PDF 全文处理、Selector 和 Research Analyst。renderer 只经 preload API 与 Electron 主进程通信；真实研究仍走 MainAgent → MCP Client → MCP Server，不绕过既有工具链。

版本变更摘要见 [CHANGELOG](CHANGELOG.md)，交付与角色报告见[v0.2.2 交付报告](docs/v0.2.2-交付报告.md)，完整历史与证据边界见[开发日志](docs/开发日志.md)。角色与文件所有权见[分工计划](docs/分工计划.md)，接口见[架构与接口](docs/架构与接口.md)，安装/验收细节见[运行与验收](docs/运行与验收.md)。公共模型与函数规则见[开发契约](docs/开发契约.md)和[schema](docs/contracts.schema.json)。

工作区支持研究与基于已有资料的讨论、会话历史、论文与报告阅读、真实工具事件、模型设置和 artifact 打开/导出。研究阶段保持来源、原始 Top-k、全文质量、候补、引用范围和部分结果可见。

v0.2.2 重点：移除原生菜单但保留常用快捷键；运行时自由文本、错误说明和状态展示中文化；Dock 标签栏按实际宽度自适应，标签多时支持滚动和溢出菜单；论文标签绑定所属任务，切换会话后仍能读取原任务的全文/总结；活动记录改为可展开卡片并删除默认占位标签；composer 支持自增高、字数提示和实时阶段流；PDF 下载改为单候选 45 秒、单篇总预算 180 秒，避免一个慢候选耗尽整篇预算。

## 快速开始

开发环境需要 Python 3.11+、Node.js 22+ 和 npm。在 Windows PowerShell 的项目根目录安装依赖：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

该脚本准备 `.venv-v2`、Python 开发/打包依赖和 root npm workspaces。手工安装：

```powershell
python -m venv .venv-v2
.\.venv-v2\Scripts\python.exe -X utf8 -m pip install -e ".[dev,packaging]"
npm ci
```

启动源码开发模式（Vite HMR + Electron）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\dev-desktop.ps1
```

构建 renderer/Electron 并从源码打开应用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-desktop.ps1
```

该脚本执行 `npm run build` 后启动应用。Windows 发布包由 `scripts/build-desktop.ps1` 构建：它先打包 Python backend，再生成 electron-builder 配置的 Windows x64 NSIS 与 portable 安装包。macOS 构建和运行尚未在本轮验证。

## 研究模式

默认离线 Demo 使用明确标记的合成资料和响应，可在不调用真实模型的情况下核对 MCP 工具链和业务组合：

```powershell
.\.venv-v2\Scripts\python.exe -X utf8 -m paper_agent.cli --mode demo --k 5
```

Live 模式会请求真实论文来源和模型，可能产生 API 用量。配置 DeepSeek key 后再使用；OpenAlex key 可选。Demo 结果不能证明论文相关性、模型质量或真实性能提升。发生来源、全文、模型或校验错误时，系统保留 partial/failure 状态与已有产物。

## 本地保存 DeepSeek 与 OpenAlex Key

1. 在 [DeepSeek 开放平台](https://platform.deepseek.com/)创建/查看 DeepSeek API Key；需要 OpenAlex 个性化鉴权时，在 [OpenAlex API 设置](https://openalex.org/settings/api)查看自己的 key。
2. 打开应用的“设置”页，在 DeepSeek API Key 中粘贴自己的 key；如要使用 OpenAlex 个性化鉴权，再填入自己的 OpenAlex key。
3. 点击“保存设置”。默认模型为 `deepseek-flash`，API 根地址为 `https://api.deepseek.com`，thinking 默认关闭。保存只更新本机文件并显示是否已配置，不会调用模型验证，也不会回显 key。

Windows 默认 data root 与配置文件为：

```text
data_root:   %LOCALAPPDATA%\Assignment\PaperAgent
config file: %LOCALAPPDATA%\Assignment\PaperAgent\config.json
```

文档和命令中的 key 值只用占位说明，不粘贴真实凭据。配置文件包含用户保存的密钥明文，请勿分享、提交或上传。密钥框留空会保留原值；使用设置页的 clear 操作才会清除。

优先级按当前实现执行：桌面 `config.json` 中已保存的字段优先于进程环境；缺少配置字段时回退环境。进程环境优先于 `.env`；非空 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` 优先于对应 `DEEPSEEK_*`。OpenAlex 使用 `OPENALEX_API_KEY`。CLI 使用进程环境和 `.env`，不读取桌面 `config.json`。

需要另一个资料目录时，设置 `PAPER_AGENT_DESKTOP_DATA_DIR`。应用、服务和服务控制进程应使用相同值。当前打包配置不把 `.env`、`config.json` 或 user data root 加入发布资源，也不要求将密钥交给构建过程；key 在用户首次运行后写入本机资料目录。

## 任务、重连与停止

Python 服务只监听 `127.0.0.1`，使用 Bearer 鉴权。renderer 不持有服务 token。窗口关闭或 Vite HMR 只断开界面，不会取消由服务持有的研究任务；重新打开会从同一 data root 读取会话、任务和事件。要终止任务，使用应用中的显式取消操作。

服务停止命令：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\manage-service.ps1 -Action status
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\manage-service.ps1 -Action stop
```

有 queued/running 任务时，服务会拒绝停止。先等待任务结束，或从应用中显式取消任务；不要强制结束服务进程。原有 session/job 若缺少 `conversation_id`，仍会出现在历史中并标记 `read_only: true`；旧记录可读，但不能继续追加消息。详见[本机服务与历史接口](docs/架构与接口.md)。

## 检查与验收

本地检查命令：

```powershell
.\.venv-v2\Scripts\python.exe -X utf8 -m pytest -q
.\.venv-v2\Scripts\python.exe -m ruff check src tests scripts
npm run typecheck
npm run test:frontend
npm run test:electron
npm run build
npm run test:e2e
```

较大数据量档位（40/40/30/8）使用独立的、被 Git 忽略的目录；demo 档位不调用模型，live 档位必须显式确认：

```powershell
.\.venv-v2\Scripts\python.exe -X utf8 scripts\stress_run.py --dry-run --mode demo
.\.venv-v2\Scripts\python.exe -X utf8 scripts\stress_run.py --mode live --yes `
  --k 8 --source-limit 40 --candidate-limit 30 --merged-limit 30 `
  --max-llm-calls 160 --max-total-tokens 1200000 --max-chunks 10 `
  --report-budget 128000 --download-timeout 90
```

结果写入 `data/validation/v021/stress/<时间>/stress-summary.json`，包含每阶段耗时、真实计数、错误和 tool call 记录。demo 结果只能证明链路组合，不能证明真实论文质量或性能。

CI 执行 Python 测试/Ruff 与 npm typecheck/build/Electron Node 测试。代码通过、类型检查和自动测试不能代替已安装桌面应用的端到端交互检查，也不能替代 live 论文质量验收。

### 当前验收记录

| 范围 | 实际记录 |
|---|---|
| Python 单测 | 307 passed、3 skipped（外网检查默认跳过） |
| Ruff | `ruff check src tests scripts` 通过 |
| TypeScript / 构建 | `npm run typecheck`、renderer/main/preload 生产构建通过 |
| Electron / 前端单测 | Electron 8/8；前端 layout/labels/activity/papers 8/8 |
| 桌面 E2E（源码） | 16 项通过：菜单移除、Sidebar、Dock 溢出/缩放、活动卡片、论文跨会话恢复、输入框自增高、取消、窄窗口 |
| 桌面 E2E（打包） | 16/16，解包版实际启动并通过完整流程 |
| Windows 发布包 | v0.2.2 解包版已生成并通过 E2E；NSIS/portable 安装包本轮未重新生成 |
| macOS 构建/运行 | 未验证 |
| live 研究 | v0.2.2 小规模真实运行用于中文输出/PDF 预算核验；结果按真实失败保留为 partial，不宣称完整科研成功 |
| live 性能结论 | 未做同输入 ≥5 轮配对基准；不提供加速比 |

历史真实 API 检查和 Top-1 研究的 partial 结果见[开发日志](docs/开发日志.md)；不得将它们表述为本轮生产桌面 E2E 或完整研究验收。

v0.2.2 解包程序位于 `data/release/desktop-v022/win-unpacked/Paper Agent.exe`，包含已构建的 Python 服务。本机到 GitHub 的 TLS 不可用，`winCodeSign` 下载失败，打包使用本地 Electron 分发并关闭 EXE 资源编辑/签名；应用内 `ServiceInfo.version` 为 0.2.2，EXE 文件版本元数据仍显示 Electron 45.0.0。旧 `data/release/desktop-v021/` 和 `data/release/desktop/` 未覆盖。
