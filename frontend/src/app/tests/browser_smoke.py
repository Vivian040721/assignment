"""Renderer interaction smoke test with explicit DesktopAPI fixtures; no live research."""
from playwright.sync_api import sync_playwright

MOCK_API = r"""
(() => {
  const params = new URLSearchParams(location.search);
  const pending = params.get('state') === 'pending';
  const delivery = params.get('delivery') || 'result';
  const paper = {
    paper_id: 'fixture-paper', title: 'Fixture evidence paper', authors: ['Fixture Author'], year: 2026,
    abstract: 'Fixture abstract', sources: ['fixture'], status: 'converted', rank: 1,
    pdf_artifact_id: 'fixture-pdf', markdown_artifact_id: 'fixture-markdown',
    acquisition_status: 'downloaded', conversion_status: 'converted', reading_status: 'fulltext',
    selection_reason: 'Fixture selection reason', errors: [],
  };
  const duplicate = { code: 'invalid_model_output', stage: 'summary', message: 'fixture duplicate diagnostic' };
  const result = {
    run_id: 'fixture-run', idea: 'Fixture research question', query: 'fixture', mode: 'live',
    execution_mode: 'concurrent', requested_execution_mode: 'auto', top_k: 5,
    status: pending ? 'pending' : 'completed', delivery_status: pending ? null : delivery,
    papers: [paper], selected_papers: [paper],
    summaries: [{
      paper_id: paper.paper_id, title: paper.title, summary_scope: 'fulltext',
      markdown: '## Fixture research notes\n\nEvidence-bound note.', overview: 'Fixture overview', errors: [],
      evidence: [{ paper_id: paper.paper_id, pdf_artifact_id: 'fixture-pdf', page_number: 2, quote: 'Fixture quote' }],
    }],
    brief: {
      findings: [
        { statement: 'Fixture finding one', evidence: [{ paper_id: paper.paper_id, pdf_artifact_id: 'fixture-pdf', page_number: 2 }] },
        { statement: 'Fixture finding two', evidence: [{ paper_id: paper.paper_id }] },
      ],
      key_papers: [{ paper_id: paper.paper_id, selection_reason: 'Fixture key paper reason', evidence_level: 'fulltext' }],
      next_step: { action: 'Fixture single next step', rationale: 'Fixture rationale' },
      limitations: [{ description: 'Fixture limitation', impact: 'Fixture impact' }], report_artifact_id: 'fixture-report',
    },
    report: { markdown: '# Fixture canonical report\n\nFull report body.', report_artifact_id: 'fixture-report', references: [] },
    source_counts: { fixture: 1 }, metrics: { analysis_paper_ids: [paper.paper_id], report_valid: true },
    errors: [duplicate, {...duplicate}], timings: [], tool_calls: [],
  };
  const artifacts = [
    { artifact_id: 'other-report', kind: 'report', name: 'other.md' },
    { artifact_id: 'fixture-report', kind: 'report', name: 'canonical.md' },
    { artifact_id: 'fixture-pdf', kind: 'pdf', name: 'fixture.pdf', paper_id: paper.paper_id },
    { artifact_id: 'fixture-markdown', kind: 'markdown', name: 'fixture.md', paper_id: paper.paper_id },
  ];
  const conversation = {
    conversation_id: 'fixture-conversation', title: 'Fixture research', updated_at: new Date().toISOString(),
    status: pending ? 'pending' : 'completed', job_id: 'fixture-job', result_job_id: 'fixture-job',
    messages: [{ id: 'fixture-message', role: 'user', kind: 'research', content: 'Fixture research question' }], result,
  };
  const job = {
    job_id: 'fixture-job', session_id: 'fixture-session', conversation_id: conversation.conversation_id,
    action: 'research', idea: conversation.title, status: pending ? 'pending' : 'completed',
    delivery_status: pending ? null : delivery, result, conversation, events: [], artifacts,
    error: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
  };
  window.__sendCount = 0;
  window.__cancelCount = 0;
  window.__pdfReadCount = 0;
  window.__lastSent = null;
  window.paperAgent = {
    info: async () => ({ version: 'fixture', api_version: 3, pid: 1, active_jobs: pending ? 1 : 0, data_root: 'fixture', config_path: 'fixture' }),
    config: async () => ({ provider: 'fixture', base_url: 'fixture', model: 'fixture-model', api_key_configured: true,
      openalex_api_key_configured: false, thinking_enabled: false, http_proxy: '', default_top_k: 5,
      default_execution_mode: 'auto', concurrency: 3, llm_timeout: 180, http_timeout: 30 }),
    saveConfig: async values => ({ ...await window.paperAgent.config(), ...values }),
    conversations: async () => [{ conversation_id: conversation.conversation_id, title: conversation.title,
      updated_at: conversation.updated_at, status: conversation.status, job_id: job.job_id }],
    conversation: async () => conversation,
    sendMessage: async values => { window.__sendCount += 1; window.__lastSent = values; return job; },
    job: async () => job,
    cancelJob: async () => { window.__cancelCount += 1; return job; },
    subscribeJob: () => () => {},
    readArtifact: async (_job, id) => ({ text: id === 'fixture-report' ? result.report.markdown : '# Fixture markdown',
      name: id, kind: 'markdown', truncated: false }),
    readPdfArtifact: async () => {
      window.__pdfReadCount += 1;
      const bytes = new TextEncoder().encode('%PDF-1.4\n% Fixture PDF bytes for API smoke testing\n%%EOF');
      return { data: bytes, name: 'fixture.pdf', kind: 'pdf' };
    },
    openArtifact: async () => {}, exportArtifact: async () => ({ cancelled: true }),
    openExternal: async () => {}, openDataDirectory: async () => {}, copyText: async () => {}, loadBenchmark: async () => null,
  };
  if (params.get('nopdfapi') === '1') delete window.paperAgent.readPdfArtifact;
})();
"""


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def open_app(browser, query=''):
    page = browser.new_page(viewport={'width': 1440, 'height': 960})
    page.add_init_script(script=MOCK_API)
    page.goto('http://127.0.0.1:4173/' + query)
    page.wait_for_load_state('networkidle')
    page.get_by_label('研究简报').wait_for()
    return page


def run():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge', headless=True)
        try:
            page = open_app(browser)
            brief = page.get_by_label('研究简报')
            expect(brief.locator('.brief-findings li').count() == 2, 'brief must render only two real findings')
            expect(brief.locator('.brief-next-step').count() == 1, 'brief must have one next step')
            expect(brief.locator('.brief-key-papers button').count() == 1, 'brief key paper missing')
            expect(brief.get_by_role('button', name='阅读完整研究报告').count() == 1, 'canonical report entry must be single')
            expect('invalid_model_output' not in page.locator('body').inner_text(), 'internal code leaked into main reading UI')

            page.get_by_role('tab', name='研究', exact=True).click()
            paper_count = page.get_by_label('精选论文数量', exact=True)
            actual_counts = paper_count.locator('option').all_text_contents()
            expect(actual_counts == ['3 篇', '5 篇', '8 篇'], f'preferred paper counts differ: {actual_counts!r}')
            paper_count.select_option('3')
            expect(paper_count.input_value() == '3', '3-paper selection failed')
            paper_count.select_option('8')
            expect(paper_count.input_value() == '8', '8-paper selection failed')
            page.get_by_text('高级设置', exact=True).click()
            execution = page.get_by_label('研究执行方式')
            expect(execution.input_value() == 'auto', 'execution mode must default to auto')
            execution.select_option('parallel')
            execution.select_option('sequential')
            paper_count.select_option('3')
            page.get_by_label('研究问题', exact=True).fill('Fixture new research submission')
            page.get_by_role('button', name='开始研究').click()
            page.wait_for_function('window.__sendCount === 1')
            expect(page.evaluate('window.__lastSent.top_k') == 3, 'selected paper count was not sent to DesktopAPI')
            expect(page.evaluate('window.__lastSent.execution_mode') == 'sequential', 'advanced execution mode was not sent to DesktopAPI')

            page.get_by_role('button', name='阅读完整研究报告').click()
            page.get_by_text('Fixture canonical report', exact=False).wait_for()
            expect(page.locator('.report-markdown').count() == 1, 'canonical report did not render once')

            page.get_by_role('button', name='添加 Dock 面板').click()
            page.get_by_role('menuitem', name='执行记录').click()
            log = page.locator('details.execution-log')
            expect(not log.evaluate('(node) => node.open'), 'execution log must start folded')
            log.locator(':scope > summary').click()
            page.get_by_text('相同错误 × 2', exact=True).wait_for()

            brief.locator('.brief-key-papers button').click()
            page.get_by_role('tab', name='研究笔记').wait_for()
            page.get_by_role('button', name='跳到 PDF 第 2 页').click()
            page.locator('.pdf-frame').wait_for()
            expect(page.get_by_label('PDF 页码').input_value() == '2', 'evidence page did not jump to PDF page 2')
            expect(page.evaluate('window.__pdfReadCount') >= 1, 'PDF bytes were not read through the controlled API')
            page.get_by_role('button', name='下一页').click()
            expect(page.get_by_label('PDF 页码').input_value() == '3', 'next page failed')
            page.get_by_role('button', name='上一页').click()
            expect(page.get_by_label('PDF 页码').input_value() == '2', 'previous page failed')
            page.get_by_label('PDF 页码').fill('4')
            page.get_by_role('button', name='跳转', exact=True).click()
            expect(page.get_by_label('PDF 页码').input_value() == '4', 'page jump failed')
            page.get_by_role('button', name='放大 PDF').click()
            page.get_by_text('125%', exact=True).wait_for()
            page.get_by_role('button', name='缩小 PDF').click()
            page.get_by_text('100%', exact=True).wait_for()
            page.get_by_label('搜索 PDF 原文').fill('fixture')
            page.get_by_role('button', name='查找', exact=True).click()
            page.locator('.pdf-search-note').wait_for()
            page.get_by_role('button', name='扩大 PDF 阅读').click()
            expect(page.locator('.workspace-focus-reading').count() == 1, 'expanded reading mode failed')
            page.get_by_role('button', name='退出专注阅读').click()
            page.get_by_role('tab', name='研究笔记').click()
            more = page.locator('details.reader-more')
            expect(not more.evaluate('(node) => node.open'), 'external actions must start in secondary menu')
            more.locator(':scope > summary').click()
            page.get_by_role('button', name='在外部打开 PDF').wait_for()
            page.get_by_role('button', name='下载 PDF').wait_for()
            page.close()

            for status, label in [('result', '研究结果'), ('preliminary_notes', '初步研究笔记'), ('no_result', '未形成结果')]:
                status_page = open_app(browser, '?delivery=' + status)
                expect(status_page.locator('.research-brief .delivery-badge').inner_text() == label, 'wrong delivery label: ' + status)
                status_page.close()

            legacy = open_app(browser, '?nopdfapi=1')
            legacy.locator('.brief-key-papers button').click()
            legacy.get_by_role('tab', name='PDF 原文').click()
            legacy.get_by_text('当前桌面运行时没有 readPdfArtifact 接口', exact=False).wait_for()
            expect(legacy.locator('.pdf-frame').count() == 0, 'legacy runtime must not substitute text for PDF bytes')
            legacy.close()

            pending = browser.new_page()
            pending.add_init_script(script=MOCK_API)
            pending.goto('http://127.0.0.1:4173/?state=pending')
            pending.wait_for_load_state('networkidle')
            pending.get_by_text('研究任务', exact=False).wait_for()
            pending.reload()
            pending.wait_for_load_state('networkidle')
            expect(pending.evaluate('window.__sendCount') == 0, 'recovery submitted a duplicate task')
            expect(pending.evaluate('window.__cancelCount') == 0, 'recovery cancelled the backend task')
            pending.close()
            print('BROWSER_SMOKE_OK: brief/config/delivery/log/report/pdf-controls/recovery')
        finally:
            browser.close()


if __name__ == '__main__':
    run()
