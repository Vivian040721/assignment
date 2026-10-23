import pytest

from paper_agent.agents.selector import validate_selection
from paper_agent.pipeline.ranking import deduplicate, rank
from paper_agent.schemas import PaperRecord
from paper_agent.sources.arxiv import parse_feed
from paper_agent.sources.openalex import parse_work, restore_abstract


def paper(identifier, **kwargs):
    return PaperRecord(paper_id=identifier, title=kwargs.pop("title", "Agent Memory"), **kwargs)


def test_cross_source_doi_merge_preserves_public_link_and_provenance():
    left = paper(
        "a", doi="HTTPS://doi.org/10.123/ABC", sources=["arxiv"], source_records=[{"id": "a"}]
    )
    right = paper(
        "b",
        doi="10.123/abc",
        sources=["openalex"],
        source_records=[{"id": "b"}],
        oa_pdf_url="https://example.org/paper.pdf",
    )
    merged = deduplicate([left, right])
    assert len(merged) == 1
    assert merged[0].sources == ["arxiv", "openalex"]
    assert len(merged[0].source_records) == 2
    assert merged[0].oa_pdf_url.endswith("paper.pdf")
    assert left.sources == ["arxiv"]  # Input objects must remain unchanged.


def test_versions_merge_but_different_doi_papers_stay_distinct():
    assert (
        len(deduplicate([paper("a", arxiv_id="2601.00001v1"), paper("b", arxiv_id="2601.00001v2")]))
        == 1
    )
    assert len(deduplicate([paper("a", doi="10.x/a"), paper("b", doi="10.x/b")])) == 2


def test_same_title_without_author_evidence_is_not_enough():
    assert len(deduplicate([paper("a"), paper("b")])) == 2


def test_keyword_ranking_keeps_query_related_item_first():
    result = rank([paper("b", title="Vision Classification"), paper("a")], "agent memory")
    assert result[0].paper_id == "a"
    assert result[0].base_score > result[1].base_score


@pytest.mark.parametrize("ids", [["invented"], ["a", "a"]])
def test_selector_rejects_invented_or_duplicate_ids(ids):
    with pytest.raises(ValueError):
        validate_selection(
            {
                "papers": [
                    {"paper_id": identifier, "rank": 1, "selection_reason": "related"}
                    for identifier in ids
                ]
            },
            [paper("a"), paper("b")],
            len(ids),
        )


def test_openalex_missing_fields_and_abstract_positions():
    assert restore_abstract({"memory": [1], "agent": [0]}) == "agent memory"
    result = parse_work({"id": "https://openalex.org/W1", "display_name": "A"})
    assert result.abstract == ""
    assert result.oa_pdf_url is None


def test_arxiv_parser_retains_version_and_pdf():
    xml = """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
    <id>https://arxiv.org/abs/2601.00001v2</id><title>Agent\nMemory</title>
    <published>2026-01-01</published><summary>Abstract.</summary>
    <author><name>A</name></author>
    <link title="pdf" href="https://arxiv.org/pdf/2601.00001v2"/>
    </entry></feed>"""
    result = parse_feed(xml)[0]
    assert result.arxiv_id == "2601.00001v2"
    assert result.title == "Agent Memory"
    assert result.year == 2026
    assert result.oa_pdf_url.endswith("v2")
