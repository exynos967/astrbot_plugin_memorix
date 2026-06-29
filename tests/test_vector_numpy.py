import numpy as np
from astrbot_plugin_memorix.memorix.storage.vector_numpy import NumpyVectorBackend, deterministic_vector


def test_deterministic_vector_stable():
    v1 = deterministic_vector("hello", 16)
    v2 = deterministic_vector("hello", 16)
    assert np.allclose(v1, v2)


def test_numpy_backend_add_search(tmp_path):
    backend = NumpyVectorBackend(dimension=8, data_dir=tmp_path)
    ids = ["a", "b"]
    vecs = np.vstack([deterministic_vector("a", 8), deterministic_vector("b", 8)]).astype(np.float32)
    backend.add(vecs, ids)
    q = deterministic_vector("a", 8)
    top_ids, _ = backend.search(q, k=1)
    assert top_ids[0] == "a"

