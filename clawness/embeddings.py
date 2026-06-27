"""
Optional semantic ranker for Clawness.

Adds true vector similarity on top of the lexical (BM25 + TF-IDF) and concept
layers. Uses model2vec static embeddings — the neat lightweight option:

  * only major dependency is numpy (no PyTorch / TensorFlow)
  * model files are ~8-30 MB on disk
  * CPU inference is hundreds of times faster than a transformer encoder

It is entirely optional. If model2vec (and a model) aren't installed, or if
WRIT_NO_SEMANTIC is set (WRIT_DISABLE_EMBEDDINGS is a legacy alias), retrieval
falls back silently to the lexical + concept pipeline. To enable:

    pip install model2vec
    # the default model is pulled on first use; override with WRIT_EMBED_MODEL

Rule vectors are cached on disk keyed by (model name + corpus hash) so the
per-call cost in a hook is just one short query encode, not a full re-embed.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional, Sequence

DEFAULT_MODEL = "minishlab/potion-base-8M"


class Model2VecEmbedder:
    """Thin wrapper around a model2vec StaticModel. Degrades to unavailable
    instead of raising if the package or model can't be loaded."""

    def __init__(self, model_name: Optional[str] = None) -> None:
        self.name = model_name or os.environ.get("WRIT_EMBED_MODEL", DEFAULT_MODEL)
        self.available = False
        self._model = None
        try:
            from model2vec import StaticModel  # type: ignore

            self._model = StaticModel.from_pretrained(self.name)
            self.available = True
        except Exception:
            # Not installed, offline, or model missing — caller will fall back.
            self.available = False

    def embed(self, texts: Sequence[str]):
        """Return an (n, dim) float32 array of L2-normalized row vectors."""
        import numpy as np

        vecs = np.asarray(self._model.encode(list(texts)), dtype="float32")
        if vecs.ndim == 1:
            vecs = vecs.reshape(1, -1)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vecs / norms


def get_default_embedder() -> Optional[Model2VecEmbedder]:
    """Auto-detect an embedder. Returns None when embeddings are unavailable
    or explicitly disabled, so callers can treat None as 'lexical only'."""
    # WRIT_NO_SEMANTIC is the documented, user-facing opt-out (it also tells the
    # bootstrap to skip installing model2vec). WRIT_DISABLE_EMBEDDINGS is kept as
    # a legacy alias.
    if os.environ.get("WRIT_NO_SEMANTIC") or os.environ.get("WRIT_DISABLE_EMBEDDINGS"):
        return None
    emb = Model2VecEmbedder()
    return emb if emb.available else None


def _cache_path(model_name: str, corpus_hash: str) -> Path:
    base = Path(
        os.environ.get("WRIT_CACHE_DIR") or Path.home() / ".cache" / "clawness"
    )
    safe_model = model_name.replace("/", "_")
    return base / f"emb-{safe_model}-{corpus_hash}.npz"


def corpus_hash(texts: Sequence[str]) -> str:
    h = hashlib.sha256()
    for t in texts:
        h.update(t.encode("utf-8", "ignore"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def build_rule_matrix(embedder: Model2VecEmbedder, texts: Sequence[str]):
    """Return an (n, dim) matrix of rule embeddings, loading from / saving to
    the on-disk cache when possible. Caching is best-effort: any I/O failure
    just falls back to recomputing in memory."""
    import numpy as np

    chash = corpus_hash([embedder.name, *texts])
    path = _cache_path(embedder.name, chash)

    try:
        if path.exists():
            data = np.load(path)
            mat = data["vectors"]
            if mat.shape[0] == len(texts):
                return mat
    except Exception:
        pass

    mat = embedder.embed(texts)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, vectors=mat)
    except Exception:
        pass

    return mat
