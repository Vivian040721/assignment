# Paper Agent v0.2.2

Current architecture: Electron + React + TypeScript + Vite is the sole desktop
UI. Python owns durable jobs, conversations, configuration and MCP execution.
Read docs/分工计划.md, docs/agent提示词.md, shared/contracts.ts and
docs/开发契约.md before implementation. Backend model fields remain frozen.

- Use gpt-6-luna with reasoning_effort=max and fork_turns=none for explicitly
  requested subagents. Give exact file ownership, inputs, output and checks.
- Coordinator owns shared contracts, root dependencies, integration and cleanup.
  Agent 8 owns the API bridge; Agent 9 owns frontend/; Agent 10 owns electron/
  and desktop packaging. Backend roles 2–7 keep their documented ownership.
- Renderer accesses only the allowlisted window.paperAgent API. Endpoint
  tokens stay in the main process; stored keys never return to the renderer.
  Keep contextIsolation, sandbox and webSecurity enabled; nodeIntegration off.
- MainAgent calls tools through a real MCP client and the official MCP SDK v2.
  Never replace protocol calls with direct tool-function calls in production.
- Reloading or closing the UI must not cancel a job or stop the daemon.
  Persist authoritative state in Python. LocalStorage holds layout only.
- Tests belong in tests/, Markdown in docs/, generated files in ignored data/.
  Current migration authorizes automated tests and actual desktop validation.
  Workers must not submit paid/live research or read real keys without a
  specific assignment. Fixtures must be labeled and cannot prove live quality.
- Preserve .env, user configuration, user research history, PDFs and reports.
  Audit imports before removing obsolete source. Do not bypass a rejected
  deletion by switching tools. Never put secrets in code, logs or artifacts.
- Keep failure/partial/fulltext boundaries honest. Performance comparisons
  require comparable real runs and imported measurements.
- No commits, pushes or publishing during this local iteration.

After work, report actual changed files, checks, failures and remaining risks.
Use npm run typecheck, npm run build, npm run test:electron and npm run test:e2e
for desktop checks; use .venv-v2 Python for pytest and Ruff.
