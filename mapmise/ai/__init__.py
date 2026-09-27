"""The shipped AI: a small sentence-embedding model that understands what an ask *means*.

Model: sentence-transformers/all-MiniLM-L6-v2 (Apache-2.0), 8-bit quantised ONNX, ~23 MB, bundled in
mapmise/ai/model. Runs offline on the CPU through onnxruntime; no API, no key, no network.

It turns a sentence into a 384-number vector; sentences with similar meaning get similar vectors.
mapmise uses that only to pick among ask rules that already exist — it cannot invent a dataset,
a scene, a date or a place.
"""

from __future__ import annotations

import threading
from functools import lru_cache
from pathlib import Path

import numpy as np

MODEL_DIR = Path(__file__).parent / "model"
MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2 (quint8 ONNX)"
_lock = threading.Lock()


class AIUnavailable(RuntimeError):
    """The model or its runtime is missing; mapmise falls back to keyword rules."""


@lru_cache(maxsize=1)
def _load():
    try:
        import onnxruntime as ort
        from tokenizers import Tokenizer
    except ImportError as e:  # pragma: no cover - optional runtime
        raise AIUnavailable(f"install onnxruntime and tokenizers to use the built-in AI ({e.name} missing)") from e
    model, tok = MODEL_DIR / "model.onnx", MODEL_DIR / "tokenizer.json"
    if not (model.exists() and tok.exists()):
        raise AIUnavailable(f"model files not found in {MODEL_DIR}")
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 2
    opts.log_severity_level = 3
    session = ort.InferenceSession(str(model), opts, providers=["CPUExecutionProvider"])
    tokenizer = Tokenizer.from_file(str(tok))
    tokenizer.enable_truncation(max_length=128)
    tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
    return session, tokenizer, {i.name for i in session.get_inputs()}


def available() -> bool:
    try:
        _load()
        return True
    except AIUnavailable:
        return False


def embed(texts: list[str]) -> np.ndarray:
    """Unit-length sentence vectors, shape (len(texts), 384): mean of token vectors, as the model was trained."""
    session, tokenizer, names = _load()
    enc = tokenizer.encode_batch(list(texts))
    ids = np.array([e.ids for e in enc], dtype=np.int64)
    mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
    feed = {"input_ids": ids, "attention_mask": mask}
    if "token_type_ids" in names:
        feed["token_type_ids"] = np.zeros_like(ids)
    with _lock:
        tokens = session.run(None, feed)[0]  # (batch, seq, 384)
    m = mask[..., None].astype(np.float32)
    vec = (tokens * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
    return vec / np.clip(np.linalg.norm(vec, axis=1, keepdims=True), 1e-12, None)
