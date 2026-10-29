/** Text search deliberately uses PDF physical page numbers (1-based). */
export interface PdfSearchHit {
  page: number;
  context: string;
}

export interface PdfTextSource {
  numPages: number;
  getPage(pageNumber: number): Promise<{
    getTextContent(): Promise<{ items: readonly unknown[] }>;
  }>;
}

export const PDF_SEARCH_MAX_HITS = 200;

export function normalizePdfSearchText(value: string): string {
  return value.normalize('NFKC').replace(/\s+/g, ' ').trim().toLocaleLowerCase();
}

/**
 * Scan pages sequentially so a superseded search stops reading the PDF quickly.
 * Never return results from a search that has been cancelled in the meantime.
 */
export async function findPdfTextMatches(
  document: PdfTextSource,
  query: string,
  isCurrent: () => boolean,
  limit = PDF_SEARCH_MAX_HITS,
): Promise<PdfSearchHit[]> {
  const term = normalizePdfSearchText(query);
  const hits: PdfSearchHit[] = [];
  if (!term || limit <= 0) return hits;

  for (let pageNumber = 1; pageNumber <= document.numPages && hits.length < limit; pageNumber += 1) {
    if (!isCurrent()) return [];
    const pdfPage = await document.getPage(pageNumber);
    if (!isCurrent()) return [];
    const content = await pdfPage.getTextContent();
    if (!isCurrent()) return [];

    const text = normalizePdfSearchText(content.items.map(item => {
      if (!item || typeof item !== 'object' || !('str' in item)) return '';
      return typeof item.str === 'string' ? item.str : '';
    }).join(' '));

    let offset = 0;
    while (hits.length < limit) {
      const index = text.indexOf(term, offset);
      if (index < 0) break;
      hits.push({
        page: pageNumber,
        context: text.slice(Math.max(0, index - 45), Math.min(text.length, index + term.length + 90)),
      });
      offset = index + term.length;
    }
  }
  return isCurrent() ? hits : [];
}
