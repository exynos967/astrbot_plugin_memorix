from __future__ import annotations

from types import SimpleNamespace

from astrbot_plugin_memorix.memorix.amemorix.services.delete_service import DeleteService


class _FakeStore:
    def __init__(self) -> None:
        self.deleted: list[list[str]] = []
        self.saved = False
        self.cleared = False

    def delete(self, ids):
        tokens = list(ids)
        self.deleted.append(tokens)
        return len(tokens)

    def save(self) -> None:
        self.saved = True

    def clear(self) -> None:
        self.cleared = True


def test_delete_vectors_hits_dual_pools() -> None:
    legacy = _FakeStore()
    paragraph = _FakeStore()
    graph = _FakeStore()
    ctx = SimpleNamespace(
        vector_store=legacy,
        paragraph_vector_store=paragraph,
        graph_vector_store=graph,
        _dual_vector_pools_ready=True,
    )
    ctx._dual_vector_pools_enabled = lambda: True
    service = DeleteService(ctx)

    deleted = service._delete_vectors(
        paragraph_hashes=["p1"],
        entity_hashes=["e1"],
        relation_hashes=["r1"],
    )

    assert deleted == 6
    assert legacy.deleted == [["p1", "e1", "r1"]]
    assert paragraph.deleted == [["p1"]]
    assert graph.deleted == [["entity:e1", "relation:r1"]]
    assert paragraph.saved is True
    assert graph.saved is True
