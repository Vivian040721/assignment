# Agent 提示词 · v0.3.0

本文件定义 Paper Agent v0.3.0 本地迭代的子 Agent 角色、输入输出和边界。所有角色以现有仓库为唯一基线，不重写 Electron/Python/MCP 架构。后续开发统一使用长期分支 `dev`，通过 PR 提交到 `main`，不新建版本号命名的开发分支。

## 统一约束

- 先读 `AGENTS.md`、`docs/开发契约.md`、`shared/contracts.ts` 和本文件；后端模型字段保持冻结。
- 只修改分配的文件；跨边界契约、版本号、根依赖和集成由协调者负责。
- renderer 只经 `window.paperAgent`；token 不进入 renderer，密钥不写入日志/文档。
- 真实研究必须经 MainAgent → MCP Client → MCP Server；不得直接调用检索函数替代协议。
- 关闭/刷新 UI 不取消服务端任务；只有显式 cancel 才取消。
- `status`、`summary_scope`、`paper.status`、`ErrorInfo.code`、MCP 工具名、JSON 字段名是机器码，不得翻译；自由文本用简体中文。
- 失败、partial、abstract-only、demo_fixture 必须如实保留，不能为了“完整”升级。
- 测试放 `tests/`，运行产物放被忽略的 `data/`；不删除用户数据、历史、PDF、报告、`.env`。
- 只有实际运行的命令才能写为通过；区分 mock、离线真实来源、完整桌面 E2E。

## 十个角色

| 角色 | 职责 | 独占文件/交付 |
|---|---|---|
| 1 协调/契约 | 接口冻结、版本、配置、集成与最终验收 | `shared/contracts.ts`、根 manifests、`docs/开发契约.md`、README/CHANGELOG |
| 2 arXiv | 检索、分页、限流、错误与候选数量 | `src/paper_agent/sources/arxiv_source.py` 及测试 |
| 3 OpenAlex | 检索、OA 地址、分页、错误 | `sources/openalex_source.py` 及测试 |
| 4 检索聚合 | 去重、来源保留、初排、查询规划 | `services/retrieval.py`、`evaluation/**`、对应测试 |
| 5 Top-k | LLM 二次筛选、结构化校验、降级标记 | `agents/selector_agent.py` 及测试 |
| 6 PDF/全文 | 安全下载、超时、PDF→Markdown、质量与页码 | `services/downloader.py`、`pdf_converter.py`、`pdf_runtime.py`、对应测试 |
| 7 研究分析 | 单篇总结、综合报告、证据/引用校验 | `agents/research_agent.py` 及测试 |
| 8 Bridge/MCP | 任务、SSE、会话、artifact、服务生命周期 | `main_agent.py`、`server.py`、`service_daemon.py`、`web.py`、`conversations.py`、`api_bridge.py` |
| 9 Renderer | React、Dock、Sidebar、会话、论文/报告/活动 UI | `frontend/src/**`、`frontend/vite.config.ts`、前端测试 |
| 10 Electron/质量 | IPC、安全、快捷键、E2E、打包、清理 | `electron/**`、`tests/electron/**`、`tests/e2e/**`、`electron-builder.config.cjs` |

## 每个角色的派工格式

1. 目标与用户可见结果。
2. 允许修改的独占文件。
3. 输入契约/现有代码位置。
4. 必须保留的失败/证据边界。
5. 最小验收命令。
6. 实际修改文件、实际测试结果、未执行项和风险。

## 既有行为基线（v0.2.2）

- 删除原生菜单；保留常用编辑快捷键。
- 运行时自由文本中文化，同时保持机器码稳定。
- Dock 标签栏根据实际宽度自适应，溢出时滚动/菜单可达。
- 论文详情按所属 job 恢复，Top-k/全文/Markdown/总结/报告可直接查看。
- 活动记录使用可展开卡片；去掉无意义默认占位。
- composer 自增高、字数提示、实时阶段流。
- PDF 双层超时：单候选 45 秒、单篇总预算 180 秒，错误保留 code 并中文展示。
