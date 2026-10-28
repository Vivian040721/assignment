import type { EvidenceReference } from '../types/contracts';

export const PDF_ZOOM_MIN = 50;
export const PDF_ZOOM_MAX = 200;
export const PDF_ZOOM_STEP = 25;

export function normalizePdfPage(value: number | string): number {
  const parsed = typeof value === 'number' ? value : Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? Math.max(1, Math.floor(parsed)) : 1;
}

export function normalizePdfZoom(value: number): number {
  if (!Number.isFinite(value)) return 100;
  return Math.max(PDF_ZOOM_MIN, Math.min(PDF_ZOOM_MAX, Math.round(value)));
}

export function canLocatePdfEvidence(evidence: EvidenceReference): boolean {
  return Boolean(
    evidence.pdf_artifact_id
    && typeof evidence.page_number === 'number'
    && Number.isInteger(evidence.page_number)
    && evidence.page_number >= 1,
  );
}

/** Chromium's native PDF viewer consumes page/zoom; search support is runtime-dependent. */
export function pdfViewerUrl(objectUrl: string, page: number, zoom: number, search = ''): string {
  const parameters = new URLSearchParams({
    page: String(normalizePdfPage(page)),
    zoom: String(normalizePdfZoom(zoom)),
    toolbar: '1',
    navpanes: '0',
  });
  if (search.trim()) parameters.set('search', search.trim());
  return `${objectUrl}#${parameters.toString()}`;
}
