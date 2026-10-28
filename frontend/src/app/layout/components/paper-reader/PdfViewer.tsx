import { useEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, FileWarning, LoaderCircle, Maximize2, Search, ZoomIn, ZoomOut } from 'lucide-react';
import type { PDFDocumentLoadingTask, PDFDocumentProxy } from 'pdfjs-dist';
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import type { Artifact } from '../../../types/contracts';
import { desktopApi } from '../../../services/desktopApi';
import { normalizePdfPage, normalizePdfZoom, PDF_ZOOM_STEP } from '../../../utils/pdf';
import { layoutStore } from '../../stores/layoutStore';

interface SearchHit {
  page: number;
  context: string;
}

const MAX_SEARCH_HITS = 200;

function normalizedSearchText(value: string): string {
  return value.normalize('NFKC').replace(/\s+/g, ' ').trim().toLocaleLowerCase();
}

function pdfErrorMessage(error: unknown): string {
  const name = error instanceof Error ? error.name : '';
  if (name === 'PasswordException') return '该 PDF 受密码保护，无法在应用内打开。';
  if (name === 'InvalidPDFException') return 'PDF 文件格式无效或已经损坏。';
  const message = error instanceof Error ? error.message : String(error);
  return message ? `PDF 读取失败：${message}` : 'PDF 读取失败，请稍后重试或在外部打开。';
}

export function PdfViewer({ jobId, artifact, initialPage = 1 }: {
  jobId: string;
  artifact: Artifact;
  initialPage?: number;
}) {
  const [document, setDocument] = useState<PDFDocumentProxy | null>(null);
  const [numPages, setNumPages] = useState(0);
  const [page, setPage] = useState(() => normalizePdfPage(initialPage));
  const [pageInput, setPageInput] = useState(String(normalizePdfPage(initialPage)));
  const [zoom, setZoom] = useState(100);
  const [loadingDocument, setLoadingDocument] = useState(true);
  const [renderingPage, setRenderingPage] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchInput, setSearchInput] = useState('');
  const [searchTerm, setSearchTerm] = useState('');
  const [searchHits, setSearchHits] = useState<SearchHit[]>([]);
  const [searchIndex, setSearchIndex] = useState(-1);
  const [searching, setSearching] = useState(false);
  const [searchMessage, setSearchMessage] = useState('');
  const [stageWidth, setStageWidth] = useState(0);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const renderTaskRef = useRef<{ cancel: () => void } | null>(null);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(entries => {
      const width = entries[0]?.contentRect.width ?? 0;
      setStageWidth(previous => Math.abs(previous - width) > 1 ? width : previous);
    });
    observer.observe(stage);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let current = true;
    let loadingTask: PDFDocumentLoadingTask | null = null;
    setLoadingDocument(true);
    setError(null);
    setDocument(null);
    setNumPages(0);
    setSearchHits([]);
    setSearchIndex(-1);
    setSearchTerm('');
    setSearchMessage('');
    const api = desktopApi();
    if (typeof api.readPdfArtifact !== 'function') {
      setLoadingDocument(false);
      setError('当前桌面运行时没有 readPdfArtifact 接口，不能读取 PDF。请重启应用后再试。');
      return () => { current = false; };
    }
    void api.readPdfArtifact(jobId, artifact.artifact_id).then(async value => {
      if (value.kind !== 'pdf' || value.data.byteLength === 0) {
        throw new Error('服务返回的 PDF 字节为空或类型不正确。');
      }
      const bytes = new Uint8Array(value.data.byteLength);
      bytes.set(value.data);
      const pdfjs = await import('pdfjs-dist');
      pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
      loadingTask = pdfjs.getDocument({ data: bytes });
      const pdf = await loadingTask.promise;
      if (!current) {
        await loadingTask.destroy();
        return;
      }
      const safeInitialPage = Math.min(normalizePdfPage(initialPage), pdf.numPages);
      setDocument(pdf);
      setNumPages(pdf.numPages);
      setPage(safeInitialPage);
      setPageInput(String(safeInitialPage));
    }).catch(reason => {
      if (current) setError(pdfErrorMessage(reason));
    }).finally(() => {
      if (current) setLoadingDocument(false);
    });
    return () => {
      current = false;
      renderTaskRef.current?.cancel();
      if (loadingTask) void loadingTask.destroy();
    };
  }, [jobId, artifact.artifact_id]);

  useEffect(() => {
    if (!document || !numPages) return;
    const next = Math.min(normalizePdfPage(initialPage), numPages);
    setPage(next);
    setPageInput(String(next));
  }, [document, initialPage, numPages]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!document || !canvas || stageWidth <= 0) return;
    let current = true;
    setRenderingPage(true);
    setError(null);
    const render = async () => {
      const pdfPage = await document.getPage(page);
      if (!current) return;
      const unscaled = pdfPage.getViewport({ scale: 1 });
      const fitScale = Math.min(1.6, Math.max(0.4, (stageWidth - 32) / unscaled.width));
      const viewport = pdfPage.getViewport({ scale: fitScale * (zoom / 100) });
      const ratio = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
      canvas.width = Math.max(1, Math.floor(viewport.width * ratio));
      canvas.height = Math.max(1, Math.floor(viewport.height * ratio));
      canvas.style.width = `${Math.floor(viewport.width)}px`;
      canvas.style.height = `${Math.floor(viewport.height)}px`;
      const task = pdfPage.render({
        canvas,
        viewport,
        transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
      });
      renderTaskRef.current = task;
      await task.promise;
    };
    void render().catch(reason => {
      const name = reason instanceof Error ? reason.name : '';
      if (current && name !== 'RenderingCancelledException') setError(pdfErrorMessage(reason));
    }).finally(() => {
      if (current) setRenderingPage(false);
    });
    return () => {
      current = false;
      renderTaskRef.current?.cancel();
    };
  }, [document, page, zoom, stageWidth]);

  const goToPage = (value: number | string) => {
    const next = Math.max(1, Math.min(numPages || 1, normalizePdfPage(value)));
    setPage(next);
    setPageInput(String(next));
  };

  const goToSearchHit = (index: number) => {
    const hit = searchHits[index];
    if (!hit) return;
    setSearchIndex(index);
    goToPage(hit.page);
  };

  const runSearch = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!document) return;
    const term = normalizedSearchText(searchInput);
    if (!term) {
      setSearchTerm('');
      setSearchHits([]);
      setSearchIndex(-1);
      setSearchMessage('');
      return;
    }
    setSearching(true);
    setSearchMessage('');
    setSearchTerm(term);
    setSearchHits([]);
    setSearchIndex(-1);
    const hits: SearchHit[] = [];
    try {
      for (let pageNumber = 1; pageNumber <= document.numPages && hits.length < MAX_SEARCH_HITS; pageNumber += 1) {
        const pdfPage = await document.getPage(pageNumber);
        const content = await pdfPage.getTextContent();
        const text = normalizedSearchText(content.items.map(item => 'str' in item ? item.str : '').join(' '));
        let from = 0;
        while (hits.length < MAX_SEARCH_HITS) {
          const at = text.indexOf(term, from);
          if (at < 0) break;
          hits.push({
            page: pageNumber,
            context: text.slice(Math.max(0, at - 45), Math.min(text.length, at + term.length + 90)),
          });
          from = at + term.length;
        }
      }
      setSearchHits(hits);
      if (hits.length) {
        setSearchIndex(0);
        goToPage(hits[0].page);
        setSearchMessage(`找到 ${hits.length} 处匹配，已定位到第 1 处。`);
      } else {
        setSearchMessage('没有找到匹配文本。');
      }
    } catch (reason) {
      setSearchMessage(pdfErrorMessage(reason));
    } finally {
      setSearching(false);
    }
  };

  const clearSearch = () => {
    setSearchInput('');
    setSearchTerm('');
    setSearchHits([]);
    setSearchIndex(-1);
    setSearchMessage('');
  };

  return <section className="pdf-reader" aria-label={`${artifact.name} PDF 原文`}>
    <div className="pdf-toolbar">
      <div className="pdf-page-controls" aria-label="PDF 翻页">
        <button className="icon-button" type="button" aria-label="上一页" title="上一页" disabled={!document || page <= 1} onClick={() => goToPage(page - 1)}><ChevronLeft size={16}/></button>
        <form onSubmit={event => { event.preventDefault(); goToPage(pageInput); }}>
          <label><span className="sr-only">PDF 页码</span><input aria-label="PDF 页码" inputMode="numeric" pattern="[0-9]*" min={1} max={numPages || undefined} type="number" value={pageInput} disabled={!document} onChange={event => setPageInput(event.target.value)}/></label>
          <span className="pdf-page-total">{numPages ? `/ ${numPages}` : ''}</span>
          <button className="subtle-button" type="submit" disabled={!document}>跳转</button>
        </form>
        <button className="icon-button" type="button" aria-label="下一页" title="下一页" disabled={!document || page >= numPages} onClick={() => goToPage(page + 1)}><ChevronRight size={16}/></button>
      </div>
      <div className="pdf-zoom-controls" aria-label="PDF 缩放">
        <button className="icon-button" type="button" aria-label="缩小 PDF" title="缩小" disabled={!document || zoom <= 50} onClick={() => setZoom(value => normalizePdfZoom(value - PDF_ZOOM_STEP))}><ZoomOut size={16}/></button>
        <span>{zoom}%</span>
        <button className="icon-button" type="button" aria-label="放大 PDF" title="放大" disabled={!document || zoom >= 200} onClick={() => setZoom(value => normalizePdfZoom(value + PDF_ZOOM_STEP))}><ZoomIn size={16}/></button>
      </div>
      <button className="icon-button" type="button" aria-label="扩大 PDF 阅读" title="扩大阅读（Esc 退出）" disabled={!document} onClick={() => layoutStore.setFocusReading(true)}><Maximize2 size={16}/></button>
    </div>
    <form className="pdf-search" onSubmit={event => void runSearch(event)}>
      <Search size={14}/><input aria-label="搜索 PDF 原文" value={searchInput} disabled={!document || searching} onChange={event => setSearchInput(event.target.value)} placeholder="搜索 PDF 原文"/>
      <button className="subtle-button" type="submit" disabled={!document || searching || !searchInput.trim()}>{searching ? '查找中…' : '查找'}</button>
      {searchTerm && <button className="inline-link-button" type="button" onClick={clearSearch}>清除</button>}
      {searchHits.length > 0 && <>
        <button className="subtle-button" type="button" disabled={searchIndex <= 0} onClick={() => goToSearchHit(searchIndex - 1)}>上一处</button>
        <span className="pdf-search-count">{searchIndex + 1} / {searchHits.length}</span>
        <button className="subtle-button" type="button" disabled={searchIndex >= searchHits.length - 1} onClick={() => goToSearchHit(searchIndex + 1)}>下一处</button>
      </>}
    </form>
    {searchMessage && <p className="pdf-search-note" role="status">{searchMessage}{searchHits[searchIndex] ? ` …${searchHits[searchIndex].context}…` : ''}</p>}
    <p className="pdf-page-note">第 {page} 页，共 {numPages || '—'} 页；页码从 1 开始，对应 PDF 物理页。</p>
    <div className="pdf-stage" ref={stageRef}>
      {loadingDocument && <div className="pdf-loading" aria-live="polite"><LoaderCircle size={18} className="spin"/><span>正在通过桌面安全接口读取 PDF…</span></div>}
      {error && <div className="pdf-unavailable" role="alert"><FileWarning size={20}/><strong>PDF 原文不可读</strong><p>{error}</p><p>请尝试在次级菜单中在外部打开或下载该 PDF。</p></div>}
      {document && !error && <>
        <canvas ref={canvasRef} className={`pdf-canvas${renderingPage ? ' is-rendering' : ''}`}/>
        {renderingPage && <div className="pdf-rendering" aria-live="polite"><LoaderCircle size={16} className="spin"/><span>正在渲染第 {page} 页…</span></div>}
      </>}
    </div>
  </section>;
}
