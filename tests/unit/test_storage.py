from uuid import uuid4

import pytest

from paper_agent.models import PipelineResult
from paper_agent.storage import PipelineStore


def test_persisted_pipeline_survives_store_reopen(tmp_path):
    result = PipelineResult(idea="真实状态与部分结果必须保留", status="partial")
    PipelineStore(tmp_path).save(result)
    assert PipelineStore(tmp_path).load(result.run_id) == result
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.parametrize("identifier", ["../../outside", "/outside", "bad-id", "a/" + uuid4().hex])
def test_run_directory_rejects_traversal_without_creating_paths(tmp_path, identifier):
    with pytest.raises(ValueError):
        PipelineStore(tmp_path).directory(identifier)
    assert list(tmp_path.iterdir()) == []
