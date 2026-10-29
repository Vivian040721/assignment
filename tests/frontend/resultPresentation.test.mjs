import assert from 'node:assert/strict';
import fs from 'node:fs';
import Module, { createRequire } from 'node:module';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

// Exercise the actual presentation selection logic, without Electron or network.
const require = createRequire(import.meta.url);
const ts = require('typescript');
const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../frontend/src/app');
const originalLoader = Module._extensions['.ts'];
Module._extensions['.ts'] = (module, filename) => {
  const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    fileName: filename,
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  module._compile(output, filename);
};
let collectPapers, displayedPapers, paperMaterialNote, visibleReportDocument;
try {
  ({ collectPapers } = require(path.join(appRoot, 'utils/papers.ts')));
  ({ displayedPapers, paperMaterialNote, visibleReportDocument } = require(path.join(appRoot, 'utils/resultPresentation.ts')));
} finally {
  Module._extensions['.ts'] = originalLoader;
}

function paper(id, more = {}) {
  return { paper_id: id, title: `Paper ${id}`, authors: [], rank: 1, ...more };
}
function collection({ selected = [paper('selected')], papers = selected, metrics = {}, summaries = [] } = {}) {
  return collectPapers({ status: 'completed', selected_papers: selected, papers, metrics, summaries, errors: [] });
}

test('actually analyzed papers follow recorded IDs, not selected candidates', () => {
  const selected = paper('selected');
  const replacement = paper('replacement');
  const other = paper('other');
  const result = collection({ selected: [selected], papers: [selected, replacement, other], metrics: { analysis_paper_ids: ['replacement', 'other'] } });
  const presentation = displayedPapers(result);
  assert.equal(presentation.stage, 'analysis');
  assert.deepEqual(presentation.papers.map(item => item.paper_id), ['replacement', 'other']);
});

test('duplicate, invalid and unknown analysis IDs do not create invented papers', () => {
  const result = collection({ metrics: { analysis_paper_ids: ['selected', 'selected', 42, 'unrecognized', ''] } });
  assert.deepEqual(displayedPapers(result).papers.map(item => item.paper_id), ['selected']);
  assert.equal(displayedPapers(result).stage, 'analysis');
});

test('explicit empty analysis record keeps selected candidates but never calls them analyzed', () => {
  const result = collection({ metrics: { analysis_paper_ids: [] } });
  const presentation = displayedPapers(result);
  assert.equal(presentation.stage, 'selection');
  assert.match(presentation.description, /候选不等于已完成阅读/);
  assert.deepEqual(presentation.papers.map(item => item.paper_id), ['selected']);
});

test('missing analysis IDs in legacy result do not imply an analysis succeeded', () => {
  const presentation = displayedPapers(collection({ metrics: {} }));
  assert.equal(presentation.stage, 'selection');
  assert.match(presentation.description, /旧结果/);
});

test('unknown analysis ID falls back to labeled candidates, rather than false results', () => {
  const presentation = displayedPapers(collection({ metrics: { analysis_paper_ids: ['unknown'] } }));
  assert.equal(presentation.stage, 'selection');
  assert.deepEqual(presentation.papers.map(item => item.paper_id), ['selected']);
});

test('missing candidates and analysis produce an explicit empty state', () => {
  const presentation = displayedPapers(collection({ selected: [], papers: [], metrics: { analysis_paper_ids: [] } }));
  assert.equal(presentation.stage, 'empty');
  assert.deepEqual(presentation.papers, []);
});

test('PDF path alone is not described as a readable renderer artifact', () => {
  assert.match(paperMaterialNote(paper('a', { pdf_path: 'C:/private/doc.pdf' }), []), /仅记录 PDF 路径/);
  assert.doesNotMatch(paperMaterialNote(paper('a', { pdf_path: 'C:/private/doc.pdf' }), []), /已关联 PDF/);
  assert.match(paperMaterialNote(paper('a'), [{ kind: 'pdf', paper_id: 'a', artifact_id: 'pdf-a' }]), /已关联 PDF/);
  assert.match(paperMaterialNote(paper('a', { pdf_artifact_id: 'pdf-a' }), []), /已关联 PDF/);
  assert.match(paperMaterialNote(paper('a'), [{ kind: 'pdf', paper_id: 'b', artifact_id: 'pdf-b' }]), /未提供 PDF/);
});

test('report text, truncation and failure cannot leak across job/artifact switches', () => {
  const old = { key: 'job-old:report', text: '# Old report', truncated: true, loading: false, error: 'Old error' };
  assert.deepEqual(visibleReportDocument('', 'job-new:report', old), {
    markdown: '', truncated: false, loading: true, error: null,
  });
  assert.deepEqual(visibleReportDocument('', null, old), {
    markdown: '', truncated: false, loading: false, error: null,
  });
  assert.deepEqual(visibleReportDocument('## New inline report', 'job-new:report', old), {
    markdown: '## New inline report', truncated: false, loading: false, error: null,
  });
});

test('current artifact content preserves its true loading, error and truncation states', () => {
  const ready = { key: 'job:report', text: '# Current', truncated: true, loading: false, error: null };
  assert.deepEqual(visibleReportDocument('', ready.key, ready), {
    markdown: '# Current', truncated: true, loading: false, error: null,
  });
  const failed = { ...ready, text: '', truncated: false, error: 'Disk read failed' };
  assert.equal(visibleReportDocument('', ready.key, failed).error, 'Disk read failed');
});

test('report component reads artifacts with an abort guard and keys export confirmations', () => {
  const source = fs.readFileSync(path.join(appRoot, 'layout/components/research-report/ResearchReport.tsx'), 'utf8');
  assert.match(source, /visibleReportDocument\(inlineMarkdown, reportKey, artifactDocument\)/);
  assert.match(source, /if \(current\) setArtifactDocument/);
  assert.match(source, /return \(\) => \{ current = false; \}/);
  assert.match(source, /exported\?\.key === reportKey/);
  assert.match(source, /if \(!job \|\| !reportArtifact \|\| !reportKey \|\| inlineMarkdown\) return/);
});
