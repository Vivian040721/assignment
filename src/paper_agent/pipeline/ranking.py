"""Owner 05: deterministic baseline; optional CPU SentenceTransformer extension."""

import os
import re
from functools import lru_cache

from paper_agent.schemas import PaperRecord


def normalized_doi(value: str | None) -> str:
    return re.sub(
        r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", (value or "").strip(), flags=re.I
    ).lower()


def normalized_title(value: str) -> str:
    return "".join(char for char in value.casefold() if char.isalnum())


def same_work(left: PaperRecord, right: PaperRecord) -> bool:
    ld, rd = normalized_doi(left.doi), normalized_doi(right.doi)
    if ld and rd:
        return ld == rd
    la = re.sub(r"v\d+$", "", left.arxiv_id or "")
    ra = re.sub(r"v\d+$", "", right.arxiv_id or "")
    if la and ra and la == ra:
        return True
    shared_author = set(map(normalized_title, left.authors)) & set(
        map(normalized_title, right.authors)
    )
    same_year = left.year == right.year and left.year is not None
    return bool(
        shared_author
        and same_year
        and normalized_title(left.title)
        and normalized_title(left.title) == normalized_title(right.title)
    )


def deduplicate(papers: list[PaperRecord]) -> list[PaperRecord]:
    result: list[PaperRecord] = []
    for original in papers:
        paper = original.model_copy(deep=True)
        matches = [item for item in result if same_work(item, paper)]
        if not matches:
            result.append(paper)
            continue
        target = matches[0]
        for extra in [*matches[1:], paper]:
            target.sources = sorted(set(target.sources + extra.sources))
            target.source_records.extend(
                item for item in extra.source_records if item not in target.source_records
            )
            target.doi = target.doi or extra.doi
            target.arxiv_id = target.arxiv_id or extra.arxiv_id
            target.oa_pdf_url = target.oa_pdf_url or extra.oa_pdf_url
            if len(extra.abstract) > len(target.abstract):
                target.abstract = extra.abstract
            if extra in result:
                result.remove(extra)
    return result


@lru_cache(maxsize=1)
def embedding_model():
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        raise RuntimeError("Install optional dependencies: pip install -e .[semantic]") from None
    return SentenceTransformer(
        os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"), device="cpu"
    )


def rank(papers: list[PaperRecord], query: str, method: str = "keyword") -> list[PaperRecord]:
    ranked = [paper.model_copy(deep=True) for paper in papers]
    if method == "semantic" and ranked:
        model = embedding_model()
        vectors = model.encode(
            [query] + [p.title + "\n" + p.abstract for p in ranked], normalize_embeddings=True
        )
        scores = (vectors[1:] @ vectors[0]).tolist()
    elif method == "keyword":
        tokens = set(re.findall(r"\w+", query.casefold()))
        scores = [
            len(tokens & set(re.findall(r"\w+", (p.title + " " + p.abstract).casefold())))
            / max(1, len(tokens))
            for p in ranked
        ]
    else:
        raise ValueError(f"Unknown ranking method: {method}")
    for paper, score in zip(ranked, scores, strict=True):
        paper.base_score = round(float(score), 6)
    return sorted(ranked, key=lambda paper: (-paper.base_score, paper.paper_id))
