import assert from 'node:assert/strict';
import path from 'node:path';
import fs from 'node:fs';
import Module from 'node:module';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = relative => fs.readFileSync(path.join(appRoot, relative), 'utf8');

function load(relative) {
  const filename = path.join(appRoot, relative);
  const built = buildSync({
    entryPoints: [filename], bundle: true, platform: 'node', format: 'cjs',
    write: false, logLevel: 'silent',
  });
  const loaded = new Module(filename);
  loaded.filename = filename;
  loaded.paths = Module._nodeModulePaths(path.dirname(filename));
  loaded._compile(built.outputFiles[0].text, filename);
  return loaded.exports;
}

test('research brief keeps at most three supported findings without placeholders', () => {
  const { supportedFindings } = load('utils/researchBrief.ts');
  const finding = statement => ({ statement, evidence: [{ paper_id: 'fixture-paper' }] });
  const brief = {
    findings: [finding('one'), { statement: 'unsupported', evidence: [] }, finding('two'), finding('three'), finding('four')],
    key_papers: [], next_step: { action: 'verify' }, limitations: [],
  };
  assert.deepEqual(supportedFindings(brief).map(item => item.statement), ['one', 'two', 'three']);
  assert.deepEqual(supportedFindings({ ...brief, findings: [finding('only one')] }).length, 1);
});

test('preferred paper count and automatic execution remain explicit in the composer', () => {
  const conversation = source('layout/components/conversation/Conversation.tsx');
  for (const count of [3, 5, 8]) assert.match(conversation, new RegExp(`value=\\{${count}\\}`));
  assert.match(conversation, /aria-label="精选论文数量"/);
  assert.match(conversation, /高级设置/);
  assert.match(conversation, /value="auto">自动/);
  assert.match(conversation, /value="parallel">并行/);
  assert.match(conversation, /value="sequential">顺序/);
  const store = source('stores/workspaceStore.ts');
  assert.match(store, /executionMode: 'auto'/);
  const { requestedExecutionMode } = load('utils/researchBrief.ts');
  assert.equal(requestedExecutionMode(undefined), 'auto');
  assert.equal(requestedExecutionMode('concurrent'), 'parallel');
});

test('all three delivery states stay separate from execution completion', () => {
  const { deliveryStatusFor, deliveryStatusLabels } = load('utils/researchBrief.ts');
  assert.deepEqual(Object.keys(deliveryStatusLabels).sort(), ['no_result', 'preliminary_notes', 'result']);
  assert.equal(deliveryStatusFor({ delivery_status: 'no_result', status: 'completed' }), 'no_result');
  assert.equal(deliveryStatusFor({ status: 'completed' }, { delivery_status: 'preliminary_notes' }), 'preliminary_notes');
  assert.equal(deliveryStatusFor({ status: 'completed' }), null);
});

test('execution log is folded by default and merges identical errors with details retained', () => {
  const { groupExecutionErrors } = load('utils/executionLog.ts');
  const duplicate = { code: 'invalid_model_output', stage: 'summary', message: 'fixture invalid output' };
  const groups = groupExecutionErrors([duplicate, { ...duplicate }, { ...duplicate, message: 'different' }]);
  assert.equal(groups.length, 2);
  assert.equal(groups[0].count, 2);
  assert.equal(groups[0].items.length, 2);
  const activity = source('layout/components/activity/ActivityPanel.tsx');
  assert.match(activity, /<details className="execution-log">/);
  assert.doesNotMatch(activity, /<details className="execution-log" open/);
  assert.match(activity, /相同错误 ×/);
});

test('paper reader exposes notes and PDF views plus all native PDF navigation controls', () => {
  const reader = source('layout/components/paper-reader/PaperReader.tsx');
  assert.match(reader, /研究笔记/);
  assert.match(reader, /PDF 原文/);
  assert.match(reader, /canLocatePdfEvidence/);
  assert.match(reader, /不可定位/);
  const viewer = source('layout/components/paper-reader/PdfViewer.tsx');
  for (const label of ['上一页', '下一页', 'PDF 页码', '放大 PDF', '缩小 PDF', '搜索 PDF 原文', '扩大 PDF 阅读']) {
    assert.ok(viewer.includes(label), label);
  }
  assert.match(viewer, /readPdfArtifact/);
  assert.doesNotMatch(viewer, /readArtifact\(/);
  assert.match(viewer, /当前桌面运行时没有 readPdfArtifact 接口/);
});

test('PDF page, zoom, search and evidence location use bounded physical-page parameters', () => {
  const { canLocatePdfEvidence, normalizePdfPage, normalizePdfZoom, pdfViewerUrl } = load('utils/pdf.ts');
  assert.equal(normalizePdfPage(-4), 1);
  assert.equal(normalizePdfPage('3'), 3);
  assert.equal(normalizePdfZoom(999), 200);
  const url = pdfViewerUrl('blob:fixture', 4, 125, 'evidence term');
  assert.match(url, /page=4/);
  assert.match(url, /zoom=125/);
  assert.match(url, /search=evidence\+term/);
  assert.equal(canLocatePdfEvidence({ paper_id: 'p', pdf_artifact_id: 'a', page_number: 2 }), true);
  assert.equal(canLocatePdfEvidence({ paper_id: 'p', pdf_artifact_id: 'a', page_number: 0 }), false);
  assert.equal(canLocatePdfEvidence({ paper_id: 'p', page_number: 2 }), false);
});

test('canonical report artifact is the single report reading target', () => {
  const { reportArtifactFor } = load('utils/researchBrief.ts');
  const artifacts = [
    { artifact_id: 'other', kind: 'report', name: 'other.md' },
    { artifact_id: 'canonical', kind: 'report', name: 'canonical.md' },
  ];
  assert.equal(reportArtifactFor({ brief: { report_artifact_id: 'canonical' }, report: { report_artifact_id: 'other' } }, artifacts).artifact_id, 'canonical');
  const conversation = source('layout/components/conversation/Conversation.tsx');
  assert.match(conversation, /brief-report-link|ResearchBriefView/);
  assert.doesNotMatch(conversation, /<MarkdownContent text=\{result\.report/);
});

test('recovery covers pending and partial success without submit or cancel on cleanup', () => {
  const { isJobActive, isJobFinished } = load('utils/status.ts');
  assert.equal(isJobActive('pending'), true);
  assert.equal(isJobFinished('partial_success'), true);
  const recovery = source('hooks/useJobRecovery.ts');
  assert.match(recovery, /import\.meta\.hot\.dispose/);
  assert.match(recovery, /subscribeJob/);
  assert.match(recovery, /refreshJob/);
  assert.doesNotMatch(recovery, /sendMessage|cancelJob|cancelActiveJob/);
});
