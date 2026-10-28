# Agent 2：arXiv 数据源交付说明

- **项目**：Paper Agent（`xuexia004/assignment`）
- **负责模块**：Agent 2 — arXiv 数据源
- **负责人 GitHub 账号**：`QJ1ng`
- **交付日期**：2026-10-27
- **任务分支**：`feat/agent02-arxiv-qj1ng-pagination`
- **目标分支**：`main`
- **实现基线**：`b9121879a81191ea76bf021930a6f55b1d421a16`
- **交付方式**：个人任务分支 → Pull Request → CI → 组长审查及合并

## 1. 任务范围与修改文件

按照团队《分工与 Git 协作规范》，Agent 2 负责 arXiv 论文查询、分页、数量限制、限流、重试、元数据解析、来源追溯、去重及错误处理。

本次业务代码及测试修改仅涉及：

| 文件 | 主要改动 |
| --- | --- |
| `src/paper_agent/sources/arxiv_source.py` | 分页检索、跨页去重与版本合并、异常 URL 处理、非有限 `Retry-After` 过滤 |
| `tests/unit/test_arxiv_source.py` | 检索数量、分页、失败传播、限流、取消、异常输入及版本处理的离线测试 |

本说明是 Agent 2 的模块交付文档；未改动公共模型、公共配置、依赖与锁文件、其他 Agent 模块或根目录 `README.md`。

## 2. 功能实现

### 2.1 查询与数据模型

- 接受普通关键词与显式 arXiv 字段查询语法，由 `httpx` 编码请求参数。
- 将 Atom 结果解析为现有公共 `Paper` 模型，包含标题、作者、摘要、发表年份、DOI、arXiv 基础 ID、版本、论文摘要页及 PDF 地址等。
- 保留 `source_records` 等来源信息；同一基础 ID 的多个版本合并，使用已检索到的最高版本作为主要元数据来源。
- 公开函数接口 `search_arxiv(query, limit, *, settings=None, client=None)` 保持不变；合法 `limit` 范围仍为 1–2000。

### 2.2 分页检索

- 单次 HTTP 请求最多获取 **40 个原始 Atom 条目**，当请求超过单页容量时自动分页。
- 每页起始位置 `start` 按**实际返回的原始条目数**递增，而不是按去重后的论文数递增。
- 每页请求数量为 `min(40, 尚缺的唯一论文数, 剩余原始位置扫描预算)`。
- 满足唯一论文数量上限、遇到短页或空页时结束；跨页保留首次出现顺序并合并版本来源记录。
- 为防止异常重复数据导致无限请求：原始位置扫描上限为 `2 × limit`、最多 100 页、连续 3 页没有新增基础 ID 时终止。触发安全边界且尚未满足请求时，以结构化错误返回，不伪装为完整成功。

### 2.3 限流、重试与异常处理

- 每页及其重试调用共享请求闸门：在本进程内串行请求、请求开始间隔至少 3 秒；下载器沿用同一闸门。
- 对指定可重试 HTTP 状态码和传输异常执行有限重试；重试保留原页偏移，不会跳页。
- `Retry-After` 支持秒数与日期，对 `inf`、`NaN` 等非有限数值回退到正常退避。
- 畸形的必需论文 ID URL 产生带 `ErrorInfo` 的 `ArxivSourceError`；畸形的可选 PDF URL 跳过并继续查找合法候选。
- 后续分页发生 HTTP、网络、XML 或 API 错误时继续抛出结构化来源错误，不将已经取得的部分列表冒充检索成功。
- 请求取消能够释放共享闸门；进程之间的限流协调不属于本模块本轮实现范围。

## 3. 自动化测试与验证结果

以下为 2026-10-27 在同步最新主分支后，由本地 Codex 执行并报告的结果；**本文件不将离线 Mock 测试表述为真实 arXiv 在线验收**。

| 验证范围 | 结果 |
| --- | --- |
| Agent 2 单元测试 | **87 通过，0 失败，0 跳过** |
| Pipeline、retrieval、搜索与下载共享限流门相关测试 | **19 通过，0 失败，0 跳过** |
| 全项目 Python 测试 | **364 通过，0 失败，3 跳过** |
| Ruff：`src tests scripts` | 通过 |
| `pip check` | 无依赖冲突 |
| `git diff --check` | 通过 |

验证环境：Windows，Python 3.12.7，项目 `.venv-v2`。运行前设置 `PAPER_AGENT_ONLINE_TESTS=0`。其中 3 项跳过为在线测试。

主要测试命令（在项目根目录运行）：

```powershell
$py = '.\.venv-v2\Scripts\python.exe'
$env:PAPER_AGENT_ONLINE_TESTS = '0'
& $py -B -X utf8 -m pytest -q -p no:cacheprovider tests/unit/test_arxiv_source.py
& $py -B -X utf8 -m pytest -q -p no:cacheprovider tests/integration/test_pipeline.py tests/unit/test_retrieval.py tests/unit/test_downloader.py::test_search_and_download_share_the_same_arxiv_gate
& $py -B -X utf8 -m pytest -q -p no:cacheprovider
& $py -B -m ruff check --no-cache src tests scripts
& $py -B -m pip check
git diff --check
```

新增及扩展测试涵盖：1/10/40 单页，41/80/81/2000 跨页，短页与空页，跨页重复及高版本合并，后续页 429/503 与重试耗尽，XML/API/超时/网络错误，分页停滞与扫描预算，限流间隔，取消时释放闸门，以及畸形 URL 与 `Retry-After` 边界值。

## 4. 兼容性与协作边界

- 已同步并基于 `b9121879` 的主分支进行完整离线回归测试。
- `Paper` 和 `ErrorInfo` 公共契约、来源函数签名及已有依赖保持兼容。
- 仅 Agent 2 的两个授权 Python 文件发生业务修改；本交付文档位于模块说明范围内。
- 本次不修改 `models.py`、`config.py`、`pipeline.py`、`retrieval.py`、`shared/contracts.ts`、下载器或其他成员的文件。
- GitHub Linux / Python 3.11 和 Windows / Python 3.13 CI、组长审查及合并，需在 PR 创建后另行完成。

## 5. 已知边界与后续验证

1. 极端高重复率的检索可能触发分页扫描预算或停滞保护；系统会返回明确错误，而不是不完整的成功结果。
2. 短页、空页作为结果末尾；未进一步依据 OpenSearch 总数判断。服务端检索排序在翻页期间变化可能引起结果漂移。
3. “最高版本”仅针对本次实际读取的记录；并不保证未读取范围中不存在更新版本。
4. 共享限流门保证本进程内请求间隔，不负责多进程或多机器的全局配额。
5. 极大的有限 `Retry-After` 的全局等待预算、PDF 明确版本冲突处理仍沿用现有策略，未进行公共接口扩展。
6. 本次**未运行真实 arXiv 在线测试**；实际网络可用性、性能和线上限流效果仍需由团队按验收安排确认。

## 6. PR 交付检查

- [x] 功能与测试修改限定在 Agent 2 的两个授权文件。
- [x] 与当前主分支完成同步，并重新执行本地离线回归测试。
- [x] 未引入依赖、未修改公共契约或其他成员代码。
- [x] 未纳入密钥、个人研究数据、下载 PDF、虚拟环境或构建产物。
- [ ] 已向 GitHub 推送个人任务分支。
- [ ] 已创建以 `main` 为 base 的 Pull Request，并申请组长审查。
- [ ] GitHub CI 检查通过。
- [ ] 组长批准并合并。
