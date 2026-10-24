"""Real tiny PDFs + explicitly injected parser failures; no network or OCR."""

import asyncio
import hashlib
import threading
import time
from pathlib import Path

import pymupdf
import pytest

from paper_agent.config import Settings
from paper_agent.models import PageMapping, Paper
from paper_agent.services.pdf_converter import PDFConverter, convert_pdf, iter_chunks, read_chunks

BODY = (
    "A reproducible fixture studies memory retrieval for language model agents. "
    "It compares a reference system with a new method on a fixed evaluation set. "
    "The result is illustrative test content, not a verified scholarly claim."
)


def make_pdf(tmp_path, texts=None, *, encrypted=False):
    path = tmp_path / "fixture.pdf"
    with pymupdf.open() as document:
        for index, text in enumerate(texts or [BODY]):
            page = document.new_page()
            page.insert_text((72, 72), f"Fixture Section {index + 1}", fontsize=18)
            if text:
                page.insert_textbox(pymupdf.Rect(72, 110, 520, 700), text, fontsize=11)
        if encrypted:
            document.save(
                path, encryption=pymupdf.PDF_ENCRYPT_AES_256,
                owner_pw="fixture-owner", user_pw="fixture-user",
            )
        else:
            document.save(path)
    return path


def record(path):
    return Paper(paper_id="../../unsafe:fixture/1", title="Fixture", pdf_path=str(path))


def config(tmp_path):
    return Settings(data_dir=tmp_path / "data")


def convert(tmp_path, paper, parser=None):
    return asyncio.run(PDFConverter(settings=config(tmp_path), parser=parser).convert(paper))


def test_real_pymupdf4llm_preserves_text_and_exact_page_provenance(tmp_path):
    path = make_pdf(tmp_path, ["Abstract\n" + BODY, "Methods\n" + BODY, "References\n" + BODY])
    original = record(path)
    original.metadata["fixture"] = {"unchanged": True}
    before = original.model_dump()
    result = asyncio.run(convert_pdf(original, settings=config(tmp_path)))
    assert original.model_dump() == before
    assert result.status == "converted" and result.error is None
    quality = result.fulltext_quality
    assert quality["page_count"] == quality["extracted_pages"] == quality["text_page_count"] == 3
    assert quality["complete"] is True and quality["nonempty_ratio"] == 1.0
    assert quality["ocr_enabled"] is False
    markdown = Path(result.markdown_path).read_text(encoding="utf-8")
    assert all(word in markdown for word in ["Abstract", "Methods", "References", "reproducible"])
    assert Path(result.markdown_path).parent == (tmp_path / "data" / "markdown")
    assert Path(result.markdown_path).name == hashlib.sha256(original.paper_id.encode()).hexdigest()+".md"
    assert [mapping.page_number for mapping in result.page_map] == [1, 2, 3]
    for mapping in result.page_map:
        assert markdown[mapping.start_char:mapping.end_char] == mapping.text
    assert not list(Path(result.markdown_path).parent.glob("*.tmp"))
    assert "语义完整" in quality["warnings"][0]


def test_real_table_keeps_cell_text_and_markdown_columns(tmp_path):
    path = tmp_path / "table.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Methods and Results", fontsize=18)
        page.insert_textbox(pymupdf.Rect(72, 100, 520, 190), BODY, fontsize=11)
        for y in (220, 250, 280):
            page.draw_line((72, y), (420, y))
        for x in (72, 240, 420):
            page.draw_line((x, 220), (x, 280))
        for x, y, text in [
            (82, 240, "Method"), (250, 240, "Score"),
            (82, 270, "Fixture baseline"), (250, 270, "0.5"),
        ]:
            page.insert_text((x, y), text, fontsize=11)
        document.save(path)
    result = convert(tmp_path, record(path))
    assert result.status == "converted"
    markdown = Path(result.markdown_path).read_text(encoding="utf-8")
    assert "Fixture baseline" in markdown and "Score" in markdown and "|" in markdown


@pytest.mark.parametrize("kind,code", [
    ("missing", "missing_pdf"), ("empty", "empty_pdf"),
    ("html", "invalid_pdf"), ("broken", "invalid_pdf"),
])
def test_file_failures_clear_stale_artifacts_and_preserve_input(tmp_path, kind, code):
    path = tmp_path / "bad.pdf"
    content = {"empty": b"", "html": b"<html>Login required</html>", "broken": b"%PDF-broken"}
    if kind in content:
        path.write_bytes(content[kind])
    original = record(path)
    original.markdown_path = "stale.md"
    original.page_map = [PageMapping(page_number=1, text="stale", end_char=5)]
    original.fulltext_quality = {"complete": True}
    result = convert(tmp_path, original)
    assert result.status == "conversion_failed" and result.errors[-1].code == code
    assert result.markdown_path is None and result.page_map == []
    assert result.fulltext_quality["complete"] is False
    assert original.markdown_path == "stale.md" and original.fulltext_quality["complete"] is True


def test_password_pdf_is_explicit_failure(tmp_path):
    result = convert(tmp_path, record(make_pdf(tmp_path, encrypted=True)))
    assert result.status == "conversion_failed" and result.errors[-1].code == "encrypted_pdf"


def test_scan_without_text_is_not_claimed_complete(tmp_path):
    path = tmp_path / "scan.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50), False)
        pixmap.clear_with(128)
        page.insert_image(pymupdf.Rect(72, 72, 400, 400), pixmap=pixmap)
        document.save(path)
    result = convert(tmp_path, record(path))
    assert result.status == "conversion_failed"
    assert result.errors[-1].code == "scanned_pdf"
    assert result.fulltext_quality["suspected_scanned_pages"] == [1]
    assert result.fulltext_quality["complete"] is False and result.markdown_path is None


def test_empty_pdf_page_is_not_claimed_complete(tmp_path):
    path = tmp_path / "blank.pdf"
    with pymupdf.open() as document:
        document.new_page()
        document.save(path)
    result = convert(tmp_path, record(path))
    assert result.errors[-1].code == "no_extractable_text"
    assert result.fulltext_quality["nonempty_ratio"] == 0.0


def test_missing_middle_page_saves_only_partial_and_keeps_physical_page_numbers(tmp_path):
    def parser(document):
        return [
            {"metadata": {"page": 1}, "text": "# Abstract\n" + BODY},
            {"metadata": {"page": 3}, "text": "# References\n" + BODY},
        ]

    result = convert(tmp_path, record(make_pdf(tmp_path, [BODY] * 3)), parser)
    quality = result.fulltext_quality
    assert result.status == "partial_fulltext" and quality["complete"] is False
    assert quality["page_count"] == 3 and quality["extracted_pages"] == 2
    assert quality["missing_pages"] == [2] and quality["text_page_count"] == 2
    assert result.page_map[1].text == "" and result.page_map[2].page_number == 3
    assert Path(result.markdown_path).is_file()


def test_bad_encoding_and_source_text_loss_are_not_upgraded(tmp_path):
    path = make_pdf(tmp_path, [BODY, BODY])

    def parser(document):
        return [
            {"metadata": {"page": 1}, "text": "\ufffd" * 300},
            {"metadata": {"page": 2}, "text": BODY},
        ]

    result = convert(tmp_path, record(path), parser)
    assert result.status == "partial_fulltext"
    assert result.fulltext_quality["garbled_pages"] == [1]
    assert result.fulltext_quality["complete"] is False
    omitted = convert(tmp_path, record(path), lambda document: [
        {"metadata": {"page": n}, "text": "Short extracted text is incomplete"} for n in (1, 2)
    ])
    assert omitted.status == "conversion_failed"
    assert omitted.fulltext_quality["suspected_text_loss_pages"] == [1, 2]


@pytest.mark.parametrize("parsed", [
    "not-page-chunks", [{"text": None}], [{"text": BODY, "metadata": {"page": 0}}],
    [{"text": BODY}],
    [{"text": BODY, "metadata": {"page": True}}],
    [{"text": BODY, "metadata": {"page": 1}}] * 2,
])
def test_invalid_parser_outputs_fail_explicitly(tmp_path, parsed):
    result = convert(tmp_path, record(make_pdf(tmp_path)), lambda document: parsed)
    assert result.status == "conversion_failed"
    assert result.errors[-1].code in {"invalid_parser_output", "invalid_page_number"}


def test_parser_exception_does_not_expose_arbitrary_diagnostics(tmp_path):
    def parser(document):
        raise RuntimeError("private-path-or-token")

    result = convert(tmp_path, record(make_pdf(tmp_path)), parser)
    assert result.errors[-1].code == "parser_failed"
    assert "private-path-or-token" not in result.model_dump_json()


def test_bounded_worker_execution_is_serialized_without_blocking_event_loop(tmp_path):
    path = make_pdf(tmp_path)
    state = {"active": 0, "peak": 0, "thread": None, "ticks": 0}

    def parser(document):
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        state["thread"] = threading.current_thread().name
        time.sleep(0.05)
        state["active"] -= 1
        return [{"metadata": {"page": 1}, "text": BODY}]

    async def execute():
        converter = PDFConverter(settings=config(tmp_path), parser=parser)
        work = asyncio.gather(converter.convert(record(path)), converter.convert(record(path)))
        while not work.done():
            state["ticks"] += 1
            await asyncio.sleep(0.005)
        return await work

    results = asyncio.run(execute())
    assert all(result.status == "converted" for result in results)
    assert state["peak"] == 1 and state["thread"].startswith("paper-pdf")
    assert state["ticks"] >= 3


def test_chunks_cover_all_markdown_with_bounded_size_and_traceable_pages(tmp_path):
    path = make_pdf(tmp_path, [BODY, BODY, BODY])
    result = convert(tmp_path, record(path), lambda document: [
        {"metadata": {"page": number}, "text": f"# Section {number}\n\n" + BODY * 3}
        for number in (1, 2, 3)
    ])
    markdown = Path(result.markdown_path).read_text(encoding="utf-8")
    chunks = read_chunks(result, max_chars=240, overlap=40)
    assert len(chunks) > 3 and chunks[0]["start_char"] == 0
    assert chunks[-1]["end_char"] == len(markdown)
    cursor = 0
    for index, chunk in enumerate(chunks):
        assert chunk["chunk_id"] == index and len(chunk["text"]) <= 240
        assert markdown[chunk["start_char"]:chunk["end_char"]] == chunk["text"]
        assert chunk["start_char"] <= cursor
        cursor = chunk["end_char"]
        assert all(1 <= number <= 3 for number in chunk["page_numbers"])
    assert cursor == len(markdown)
    assert any("Section 3" in chunk["sections"] for chunk in chunks)


def test_chunks_reject_stale_mapping_and_bad_parameters(tmp_path):
    path = tmp_path / "text.md"
    path.write_text("# Real text\n\n" + BODY, encoding="utf-8")
    paper = Paper(paper_id="fixture", title="Fixture", markdown_path=str(path), page_map=[
        PageMapping(page_number=1, text="wrong", start_char=0, end_char=5),
    ])
    with pytest.raises(ValueError, match="mapping"):
        list(iter_chunks(paper))
    with pytest.raises(ValueError, match="max_chars"):
        read_chunks(paper, max_chars=100, overlap=100)
