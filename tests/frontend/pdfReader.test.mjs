import assert from 'node:assert/strict';
import path from 'node:path';
import fs from 'node:fs';
import Module from 'node:module';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const source = path => fs.readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');

function load(relative) {
  const filename = path.join(repoRoot, relative);
  const built = buildSync({ entryPoints: [filename], bundle: true, platform: 'node', format: 'cjs', write: false, logLevel: 'silent' });
  const loaded = new Module(filename);
  loaded.filename = filename;
  loaded.paths = Module._nodeModulePaths(path.dirname(filename));
  loaded._compile(built.outputFiles[0].text, filename);
  return loaded.exports;
}

const { findPdfTextMatches, normalizePdfSearchText, PDF_SEARCH_MAX_HITS } = load('frontend/src/app/utils/pdfSearch.ts');

function pdf(pages) {
  const visits = [];
  return {
    numPages: pages.length,
    visits,
    async getPage(pageNumber) {
      visits.push(pageNumber);
      return { async getTextContent() { return { items: pages[pageNumber - 1].map(str => ({ str })) }; } };
    },
  };
}

test('PDF text search normalizes case, whitespace, and Unicode, with physical 1-based pages', async () => {
  const document = pdf([['First page'], ['AI  for', '  Science and AI FOR SCIENCE'], ['third']]);
  assert.equal(normalizePdfSearchText('  ＡＩ\tFOR\nScience '), 'ai for science');
  const hits = await findPdfTextMatches(document, 'AI FOR SCIENCE', () => true);
  assert.deepEqual(hits.map(hit => hit.page), [2, 2]);
  assert.deepEqual(document.visits, [1, 2, 3]);
  assert.match(hits[0].context, /ai for science/);
});

test('PDF search skips non-text items, empty terms, and limits matches without reading unnecessary pages', async () => {
  const document = pdf([['one one one'], ['one'], ['one']]);
  assert.deepEqual(await findPdfTextMatches(document, '   ', () => true), []);
  assert.deepEqual(document.visits, []);
  const hits = await findPdfTextMatches(document, 'one', () => true, 2);
  assert.deepEqual(hits.map(hit => hit.page), [1, 1]);
  assert.deepEqual(document.visits, [1]);
  assert.equal(PDF_SEARCH_MAX_HITS, 200);
  const withNonText = { numPages: 1, async getPage() { return { async getTextContent() { return { items: [{ type: 'beginMarkedContent' }, { str: 'hello' }, null] }; } }; } };
  assert.equal((await findPdfTextMatches(withNonText, 'hello', () => true)).length, 1);
});

test('PDF search cancels after pending page reads and never scans another page', async () => {
  let finishPage;
  const pendingPage = new Promise(resolve => { finishPage = resolve; });
  const document = { numPages: 2, visits: [], async getPage(pageNumber) { this.visits.push(pageNumber); return pendingPage; } };
  let active = true;
  const search = findPdfTextMatches(document, 'target', () => active);
  active = false;
  finishPage({ async getTextContent() { throw new Error('cancelled search must not read page contents'); } });
  assert.deepEqual(await search, []);
  assert.deepEqual(document.visits, [1]);
});

test('PDF search cancels after pending text extraction without returning obsolete matches', async () => {
  let finishText;
  const pendingText = new Promise(resolve => { finishText = resolve; });
  const document = { numPages: 3, visits: [], async getPage(pageNumber) { this.visits.push(pageNumber); return { getTextContent: () => pendingText }; } };
  let active = true;
  const search = findPdfTextMatches(document, 'target', () => active);
  await Promise.resolve();
  active = false;
  finishText({ items: [{ str: 'target' }] });
  assert.deepEqual(await search, []);
  assert.deepEqual(document.visits, [1]);
});

test('PDF reader invalidates old searches on document change and on explicit clear', () => {
  const viewer = source('frontend/src/app/layout/components/paper-reader/PdfViewer.tsx');
  assert.match(viewer, /searchRequestRef\.current \+= 1;/);
  assert.match(viewer, /const request = \+\+searchRequestRef\.current/);
  assert.match(viewer, /if \(!isCurrent\(\)\) return/);
  assert.match(viewer, /setSearching\(false\)/);
  assert.match(viewer, /if \(!current\) return;\s+const pdfjs = await import/);
});

test('PDF render cancellation waits for earlier render before drawing into same canvas', () => {
  const viewer = source('frontend/src/app/layout/components/paper-reader/PdfViewer.tsx');
  assert.match(viewer, /precedingRender\?\.cancel\(\)/);
  assert.match(viewer, /await precedingRender\.promise\.catch/);
  assert.match(viewer, /if \(!current\) return;\s+const pdfPage = await document\.getPage/);
  assert.match(viewer, /renderTaskRef\.current === task/);
  assert.match(viewer, /setRenderError\(pdfErrorMessage/);
  assert.match(viewer, /重新读取/);
});

test('PDF page survives switching between notes and PDF, and evidence stays on its owning paper', () => {
  const reader = source('frontend/src/app/layout/components/paper-reader/PaperReader.tsx');
  assert.match(reader, /onPageChange=\{setRequestedPage\}/);
  assert.match(reader, /item\.paper_id !== paper\.paper_id/);
  assert.match(reader, /item\.paper_id === paper\.paper_id && canLocatePdfEvidence\(item\)/);
  assert.match(reader, /remoteJob\?\.job_id === remoteTargetId/);
  assert.match(reader, /remoteError\?\.jobId === remoteTargetId/);
});

test('expanding extracted Markdown actually requests it; merely rendering the closed panel does not', () => {
  const reader = source('frontend/src/app/layout/components/paper-reader/PaperReader.tsx');
  assert.match(reader, /if \(!showFulltext \|\| !job \|\| !markdownArtifact\) return/);
  assert.match(reader, /onToggle=\{event => \{ if \(event\.currentTarget\.open\) setShowFulltext\(true\); \}\}/);
});
