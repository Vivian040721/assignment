"""CPU PDF-to-Markdown conversion with conservative quality and page provenance.

PyMuPDF is not thread safe. All use in this module runs in a bounded worker pool
under ``PDF_PARSE_LOCK``; other in-process PyMuPDF users should share that lock.
The deterministic legacy PyMuPDF4LLM parser does not invoke OCR or model downloads.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
import unicodedata
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from paper_agent.config import Settings
from paper_agent.models import ErrorInfo, PageMapping, Paper
from paper_agent.services.pdf_runtime import PDF_PARSE_LOCK

_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="paper-pdf")
_MIN_TEXT_CHARS = 20
_LIMITATION = (
    "文本覆盖率只是启发式指标，不代表语义完整。图表、数学公式和复杂表格仍需人工核对；OCR 已关闭。"
)
PageParser = Callable[[Any], list[dict[str, Any]]]


class PDFConversionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _default_parser(document: Any) -> list[dict[str, Any]]:
    # Pinning 1.28.2 keeps this parser API stable. Importing the legacy helper
    # explicitly avoids implicit OCR and neural layout inference in new defaults.
    from pymupdf4llm.helpers.pymupdf_rag import to_markdown

    return to_markdown(
        document,
        page_chunks=True,
        show_progress=False,
        force_text=True,
        write_images=False,
        embed_images=False,
        margins=0,
        table_output="markdown",
    )


def _compact_length(text: str) -> int:
    return sum(char.isalnum() for char in text)


def _garbled_ratio(text: str) -> float:
    characters = [char for char in text if not char.isspace()]
    if not characters:
        return 0.0
    bad = sum(
        char == "\ufffd" or unicodedata.category(char) in {"Cc", "Cs", "Co", "Cn"}
        for char in characters
    )
    return bad / len(characters)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=path.name + ".", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _failure(paper: Paper, code: str, message: str) -> Paper:
    paper.status = "conversion_failed"
    paper.error = message
    paper.markdown_path = None
    paper.page_map = []
    paper.fulltext_quality.setdefault("complete", False)
    paper.fulltext_quality["complete"] = False
    paper.errors.append(ErrorInfo(
        code=code, message=message, stage="conversion", paper_id=paper.paper_id,
    ))
    return paper


def _page_chunks(parsed: Any, page_count: int) -> dict[int, str]:
    if not isinstance(parsed, list):
        raise PDFConversionError("invalid_parser_output", "解析器未返回按页内容。")
    pages: dict[int, str] = {}
    for chunk in parsed:
        if not isinstance(chunk, dict) or not isinstance(chunk.get("text"), str):
            raise PDFConversionError("invalid_parser_output", "解析器返回了无效页面。")
        metadata = chunk.get("metadata", {})
        if not isinstance(metadata, dict):
            raise PDFConversionError("invalid_parser_output", "页面元数据无效。")
        # The parser must provide provenance. List order alone cannot establish
        # physical page numbers after a skipped page.
        number = metadata.get("page", metadata.get("page_number"))
        if isinstance(number, bool) or not isinstance(number, int):
            raise PDFConversionError("invalid_page_number", "解析器页码必须是整数。")
        if not 1 <= number <= page_count or number in pages:
            raise PDFConversionError("invalid_page_number", "解析器页码重复或越界。")
        pages[number] = chunk["text"].replace("\r\n", "\n").replace("\r", "\n").strip()
    return pages


class PDFConverter:
    """Inject ``parser(document)`` for deterministic unit tests, not fake live output."""

    def __init__(self, *, settings: Settings | None = None, parser: PageParser | None = None):
        self.settings = settings or Settings.from_env()
        self.parser = parser or _default_parser

    async def convert(self, paper: Paper) -> Paper:
        # Snapshot before yielding: no nested containers of the input are mutated.
        snapshot = paper.model_copy(deep=True)
        return await asyncio.get_running_loop().run_in_executor(
            _POOL, self._convert_sync, snapshot,
        )

    def _convert_sync(self, paper: Paper) -> Paper:
        paper.fulltext_quality = {"complete": False, "page_count": 0, "extracted_pages": 0}
        try:
            if not paper.pdf_path:
                raise PDFConversionError("missing_pdf", "没有可用的 PDF 路径。")
            path = Path(paper.pdf_path)
            if not path.is_file():
                raise PDFConversionError("missing_pdf", "下载的 PDF 文件不存在。")
            size = path.stat().st_size
            if size == 0:
                raise PDFConversionError("empty_pdf", "PDF 文件为空。")
            if size > self.settings.max_pdf_bytes:
                raise PDFConversionError("pdf_too_large", "PDF 超过配置的大小限制。")
            with path.open("rb") as stream:
                if b"%PDF-" not in stream.read(1024):
                    raise PDFConversionError("invalid_pdf", "文件缺少 PDF 标识。")

            with PDF_PARSE_LOCK:
                import pymupdf

                try:
                    document = pymupdf.open(path)
                except Exception as exc:
                    raise PDFConversionError("invalid_pdf", "PDF 无法打开。") from exc
                with document:
                    if not document.is_pdf:
                        raise PDFConversionError("invalid_pdf", "文件不是有效 PDF。")
                    if document.needs_pass:
                        raise PDFConversionError("encrypted_pdf", "PDF 需要密码。")
                    page_count = document.page_count
                    paper.fulltext_quality["page_count"] = page_count
                    if page_count < 1:
                        raise PDFConversionError("empty_pdf", "PDF 没有页面。")
                    source_pages = []
                    for number, page in enumerate(document, start=1):
                        raw = page.get_text("text")
                        source_pages.append({
                            "page_number": number,
                            "source_text_chars": _compact_length(raw),
                            "source_garbled_ratio": _garbled_ratio(raw),
                            "has_images": bool(page.get_images()),
                        })
                    try:
                        pages = _page_chunks(self.parser(document), page_count)
                    except PDFConversionError:
                        raise
                    except Exception as exc:
                        raise PDFConversionError(
                            "parser_failed", "PyMuPDF4LLM could not extract this PDF.",
                        ) from exc

            contents: list[str] = []
            mappings: list[PageMapping] = []
            offset = 0
            for number in range(1, page_count + 1):
                marker = f"<!-- PDF page {number} -->\n\n"
                contents.append(marker)
                offset += len(marker)
                text = pages.get(number, "")
                mappings.append(PageMapping(
                    page_number=number, text=text, start_char=offset, end_char=offset + len(text),
                ))
                contents.extend((text, "\n\n"))
                offset += len(text) + 2
            markdown = "".join(contents)
            quality = _quality(source_pages, pages)
            paper.fulltext_quality = quality
            if quality["text_page_count"] == 0:
                if quality["suspected_scanned_pages"]:
                    code, message = "scanned_pdf", "PDF has no usable text layer; OCR is disabled."
                elif quality["garbled_pages"]:
                    code, message = "garbled_text", "PDF text is unreadable or badly encoded."
                else:
                    code, message = "no_extractable_text", "PDF has no usable extractable text."
                return _failure(paper, code, message)
            target = Path(self.settings.markdown_dir) / (
                hashlib.sha256(paper.paper_id.encode("utf-8")).hexdigest() + ".md"
            )
            _atomic_write(target, markdown)
            paper.markdown_path = str(target.resolve())
            paper.page_map = mappings
            paper.metadata["conversion"] = {
                "parser": "pymupdf4llm.legacy", "parser_version": "1.28.2",
                "ocr_enabled": False, "cache_hit": False,
                "markdown_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
            }
            if quality["complete"]:
                paper.status, paper.error = "converted", None
            else:
                paper.status = "partial_fulltext"
                paper.error = "PDF 文本覆盖不完整，请查看全文质量信息。"
                paper.errors.append(ErrorInfo(
                    code="partial_extraction", message=paper.error, stage="conversion",
                    paper_id=paper.paper_id,
                    details={"missing_pages": quality["missing_pages"],
                             "unusable_pages": quality["unusable_pages"]},
                ))
            return paper
        except PDFConversionError as exc:
            return _failure(paper, exc.code, str(exc))
        except OSError:
            return _failure(paper, "conversion_io_error", "PDF 或 Markdown 文件操作失败。")
        except Exception:
            # Return per-paper failure without logging potentially sensitive paths.
            return _failure(paper, "conversion_failed", "PDF 转换发生未预期错误。")


def _quality(source_pages: list[dict[str, Any]], pages: dict[int, str]) -> dict[str, Any]:
    page_count = len(source_pages)
    page_quality = []
    for source in source_pages:
        number = source["page_number"]
        text = pages.get(number, "")
        length = _compact_length(text)
        garbled = max(_garbled_ratio(text), source["source_garbled_ratio"]) >= 0.02
        loss = source["source_text_chars"] >= 100 and length < source["source_text_chars"] * 0.4
        page_quality.append({
            **source, "extracted": number in pages, "text_chars": length,
            "garbled_ratio": round(_garbled_ratio(text), 4), "garbled": garbled,
            "suspected_scanned": source["has_images"] and source["source_text_chars"] < 20,
            "suspected_text_loss": loss,
            "usable": length >= _MIN_TEXT_CHARS and not garbled and not loss,
        })
    usable = [page["page_number"] for page in page_quality if page["usable"]]
    missing = [page["page_number"] for page in page_quality if not page["extracted"]]
    garbled = [page["page_number"] for page in page_quality if page["garbled"]]
    scanned = [page["page_number"] for page in page_quality if page["suspected_scanned"]]
    unusable = [page["page_number"] for page in page_quality if not page["usable"]]
    loss = [page["page_number"] for page in page_quality if page["suspected_text_loss"]]
    warnings = [_LIMITATION]
    if scanned:
        warnings.append(f"疑似纯图片/扫描页（未启用 OCR）：{scanned}。")
    if garbled:
        warnings.append(f"页面可能存在编码损坏：{garbled}。")
    if missing:
        warnings.append(f"解析器未返回页面：{missing}。")
    if unusable:
        warnings.append(f"文本不足、空白或不完整页面：{unusable}。")
    if loss:
        warnings.append(f"与源文本层相比可能存在文本遗漏：{loss}。")
    return {
        "page_count": page_count, "extracted_pages": len(pages),
        "text_page_count": len(usable), "nonempty_ratio": len(usable) / page_count,
        "text_char_count": sum(page["text_chars"] for page in page_quality),
        "complete": not unusable and not missing and not scanned,
        "missing_pages": missing, "unusable_pages": unusable,
        "garbled_pages": garbled, "suspected_scanned_pages": scanned,
        "suspected_text_loss_pages": loss, "page_quality": page_quality,
        "ocr_enabled": False, "quality_scope": "text_layer_coverage_heuristic",
        "warnings": warnings,
    }


async def convert_pdf(paper: Paper, *, settings: Settings | None = None) -> Paper:
    return await PDFConverter(settings=settings).convert(paper)


def iter_chunks(
    paper: Paper, *, max_chars: int = 12000, overlap: int = 300,
) -> Iterator[dict[str, Any]]:
    """Read every persisted character with bounded chunks and original page offsets.

    ``page_map`` must be exact. This helper rejects stale mapping data instead of
    fabricating citations. It does not apply ``Settings.max_chunks`` truncation.
    File reads are synchronous; call from a worker when used in an async server.
    """
    if max_chars < 64 or overlap < 0 or overlap >= max_chars:
        raise ValueError("max_chars must be >=64 and 0 <= overlap < max_chars")
    if not paper.markdown_path:
        raise ValueError("No Markdown file is available")
    text = Path(paper.markdown_path).read_text(encoding="utf-8")
    for mapping in paper.page_map:
        if mapping.end_char > len(text) or text[mapping.start_char:mapping.end_char] != mapping.text:
            raise ValueError("Page mapping does not match persisted Markdown")
    sections = [(match.start(), match.group(1).strip()) for match in re.finditer(
        r"(?m)^#{1,6}\s+(.+)$", text,
    )]
    start, chunk_id = 0, 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            boundary = text.rfind("\n\n", start + max_chars // 2, end)
            if boundary != -1:
                end = boundary + 2
        active = [name for position, name in sections if start <= position < end]
        preceding = [name for position, name in sections if position < start]
        if preceding:
            active.insert(0, preceding[-1])
        yield {
            "chunk_id": chunk_id, "text": text[start:end],
            "start_char": start, "end_char": end,
            "page_numbers": [mapping.page_number for mapping in paper.page_map
                             if mapping.start_char < end and mapping.end_char > start],
            "sections": list(dict.fromkeys(active)),
        }
        chunk_id += 1
        if end == len(text):
            break
        start = max(start + 1, end - overlap)


def read_chunks(
    paper: Paper, *, max_chars: int = 12000, overlap: int = 300,
) -> list[dict[str, Any]]:
    return list(iter_chunks(paper, max_chars=max_chars, overlap=overlap))
