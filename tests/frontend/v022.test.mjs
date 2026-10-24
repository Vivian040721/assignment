import assert from 'node:assert/strict';
import path from 'node:path';
import Module from 'node:module';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

function load(relative) {
  const filename = path.join(root, relative);
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

test('v0.2.2 labels are Chinese and machine codes remain stable', () => {
  const labels = load('frontend/src/app/utils/labels.ts');
  assert.equal(labels.statusLabel('demo_completed'), '演示完成');
  assert.equal(labels.toolLabel('search_papers'), '检索论文');
  assert.equal(labels.errorLabel({ code: 'download_timeout', message: 'Total PDF acquisition time limit exceeded' }), 'PDF 获取总时长超限，可稍后重试或调大下载预算。');
  assert.match(labels.statusLabel('future_status'), /未知状态/);
});

test('dock tab measurement adapts and reports overflow', () => {
  const { measureDockTabs } = load('frontend/src/app/layout/components/docking/tabMetrics.ts');
  assert.deepEqual(measureDockTabs(372, 2), { width: 184, overflow: false });
  assert.deepEqual(measureDockTabs(372, 3), { width: 122, overflow: false });
  assert.deepEqual(measureDockTabs(145, 2), { width: 104, overflow: true });
  assert.equal(measureDockTabs(107, 4).overflow, true);
});

test('replacement papers are addressable by paperById while staying in replacement pairs', () => {
  const { collectPapers } = load('frontend/src/app/utils/papers.ts');
  const paper = (id, title) => ({ paper_id: id, title, authors: [], year: 2026, sources: ['arxiv'], status: 'selected' });
  const top = paper('a', 'Original paper');
  const replacement = paper('c', 'Replacement paper');
  const result = {
    papers: [top, replacement], selected_papers: [top], summaries: [], status: 'partial',
    metrics: { replacements: [{ original_paper_id: 'a', replacement_paper_id: 'c' }], analysis_paper_ids: ['c'] },
    errors: [],
  };
  const collection = collectPapers(result);
  assert.equal(collection.paperById.get('c')?.title, 'Replacement paper');
  assert.equal(collection.replacementPairs[0].replacement?.paper_id, 'c');
  assert.equal(collection.analysisPapers.some(item => item.paper_id === 'c'), false);
});

test('activity groups use Chinese stage labels and flag error groups', () => {
  const { groupEvents, groupToolCalls } = load('frontend/src/app/utils/activity.ts');
  const groups = groupEvents([
    { seq: 1, timestamp: '2026-10-24T00:00:00Z', kind: 'trace', stage: 'download', message: '下载中' },
    { seq: 2, timestamp: '2026-10-24T00:00:01Z', kind: 'error', stage: 'download', message: '下载失败' },
  ]);
  assert.equal(groups[0].label, '全文下载');
  assert.equal(groups[0].hasError, true);
  const tools = groupToolCalls([{ tool: 'search_papers', status: 'completed', duration_seconds: 1.5, arguments: {} }]);
  assert.equal(tools[0].label, '检索论文');
  assert.equal(tools[0].items.length, 1);
});
