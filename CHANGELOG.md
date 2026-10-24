# Changelog

All notable changes to Paper Agent are documented here.

## [0.2.2] - 2026-10-24

### Fixed

- Removed the native Electron application menu; common edit, reload/devtools and Quit shortcuts are preserved through a scoped window shortcut handler.
- Research output and user-facing errors now use Simplified Chinese. JSON keys, IDs, enum values, citations and source titles remain unchanged.
- Dock tabs now measure the actual panel width, adapt tab width, scroll horizontally when needed and expose an overflow menu with full titles; split groups, resize and fullscreen re-measure correctly.
- Paper tabs store their owning job ID. Switching conversation or restarting no longer makes a previously opened paper look missing; replacements are addressable through `paperById`.
- Activity records are grouped into expandable cards with redacted details, and empty event/placeholder sections are removed.
- Composer grows from 72px to 190px, shows a character count and code-point limit, allows drafting while a job runs, and displays a live recent-progress stream.
- PDF acquisition uses a 45-second per-candidate budget and a 180-second per-paper total budget, with a 5-second connect timeout and at most eight candidates. Slow candidates fall through to the next candidate instead of exhausting the whole paper.
- Removed default report/activity/settings placeholder tabs; the default Dock contains only the paper tab. v1 layouts migrate by dropping the old placeholder tabs.

### Added

- `frontend/src/app/utils/labels.ts` for Chinese status/stage/tool/kind/error labels.
- `frontend/src/app/utils/activity.ts` and expandable activity cards.
- Dock tab measurement helper and overflow menu; frontend tests for labels/tab metrics/replacement lookup/activity grouping.
- E2E coverage for menu removal, overflow tabs, activity cards, cross-conversation paper tabs, composer growth and the historical Sidebar/Dock regressions.

### Changed

- Version `0.2.2`; API remains v3.
- Prompt language: summary/report/selector prose is requested in Simplified Chinese while evidence quotes and reference titles stay verbatim.
- PDF error messages are Chinese but stable machine codes remain in `ErrorInfo.code`.

### Verification

- 307 Python tests passed, 3 skipped; Ruff passed; TypeScript/build passed; Electron 8/8; frontend 8/8; source desktop E2E 16/16. See `README.md` and `docs/运行与验收.md` for the latest live/packaging evidence.

## [0.2.1] - 2026-10-24

### Fixed

- Sidebar collapse/expand no longer clips the expand control: the rail now has one state source, a 58px layout, and a dedicated footer control that stays inside the rail.
- Closing the right Dock no longer unmounts panel state; unsaved settings, report scroll and paper tabs survive close/reopen.
- Electron now forwards backend error messages (for example API-key and active-job conflicts) instead of showing only HTTP status codes.
- SSE fatal status handling and indefinite recovery polling prevent jobs from being silently stranded.
- Job snapshots merge events by `seq`, avoiding loss of events that arrive while a GET snapshot is in flight.
- The composer no longer steals the empty welcome surface by auto-scrolling to the bottom.
- Replacement papers are listed once, and chat jobs keep access to the research job's artifacts.
- Paper detail now separates full text from the single-paper summary and surfaces scores, ranking reason, PDF/Markdown status and truncation.
- Agent tool errors are returned to the model as recoverable tool feedback instead of immediately aborting the run.
- Explicit English queries bypass the query planner; duplicate stage errors are de-duplicated; staged MainAgent runs now persist total timing.

### Added

- Light and dark semantic token sets; improved focus, contrast, typography and Markdown readability.
- Motion tokens and reduced-motion handling that keeps spinners visible.
- `scripts/stress_run.py` for labeled 40/40/30/8 profiles through the real MCP path.
- Frontend layout-store tests (`npm run test:frontend`) and E2E regressions for sidebar collapse/expand/rapid toggles/persistence and Dock state.

### Changed

- Version: `0.2.1`; API remains v3.
- Top-k validation is 1–15 and concurrency 1–8 consistently across renderer, preload and backend.
- Research input limit is 12,000 characters across renderer, preload and backend.
- Python runtime version is read from a single `__version__` source.

### Removed

- Stale PySide6/Qt source remnants and regenerable Python caches; user research data, PDFs, reports and `.env` were preserved. See `docs/运行与验收.md#清理执行记录`.

### Verification

- Python: 304 passed, 3 skipped; Ruff passed.
- Desktop: typecheck/build passed; Electron tests 8/8; frontend layout tests 2/2; source E2E 12/12; unpacked v0.2.1 E2E 12/12.
- Live small run: `partial`, 8 physical model requests, 41,501 tokens, report body 7,942 chars.
- Live 40/40/30/8 run: arXiv 40 + OpenAlex 40, 59 unique, 30 downstream, Top-8 readable, 8 summaries (5 partial, 3 failed), 56 physical model requests and 347,389 tokens. A first report regeneration hit the old input budget; after adding an independent report budget and compact evidence projection, the successful retry produced a 2,417-char overview, 13,875-char report and 5 real references. One intermediate attempt was rejected for invalid citations and preserved as `invalid_model_output`.
- Packaging: unpacked app generated with local Electron distribution. GitHub TLS was unavailable, so `winCodeSign` could not be downloaded; executable metadata editing/signing was disabled and installer targets were not rebuilt.
- Demo/mock results are not live quality or performance evidence. No paired ≥5-run benchmark was executed, so no speedup is claimed.

See `README.md` and `docs/运行与验收.md` for the full evidence boundary.
