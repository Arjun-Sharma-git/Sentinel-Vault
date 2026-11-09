"""Cross-encoder reranking with Qwen3-Reranker.

Hybrid retrieval (vectors + full-text, fused with RRF) is good at finding candidates but ranks
them coarsely: RRF only sees positions in two lists, never the question and passage together.
A cross-encoder reads each (question, passage) pair jointly and scores how well the passage
answers the question, which mostly fixes the order of the top few results. That order matters
here because small local models get only a few passages and favor the first ones.

Two kinds, both run in this process through `transformers` (Ollama has no rerank API), on the
same torch device as Laya:

  cross-encoder  a small sequence-classification model (default: MiniLM-L6, 22M parameters,
                 English). About 1 s for 20 passages on a laptop CPU. The laptop default.
  qwen           Qwen3-Reranker (0.6B+): a causal LM asked "does this document answer the
                 query? yes/no", scored as P(yes) at the final position. Multilingual and
                 stronger, but ~25x the compute: use it on a GPU (the RX 9070 XT profile).

Reranking runs *after* the permission re-check, so it only ever reads passages the user may
see (retrieval.py).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Protocol

logger = logging.getLogger(__name__)

INSTRUCTION = "Given an employee's question, judge whether this company document passage answers it"
_PREFIX = (
    "<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and "
    'the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n'
    "<|im_start|>user\n"
)
_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"


class Reranker(Protocol):
    name: str
    enabled: bool
    # Trained on English only: it reads other scripts badly enough to push the right passage
    # down (MiniLM moved the right Hindi document out of first place on 8 of 12 eval questions),
    # so the retriever skips it for questions in a non-Latin script and keeps the fused order.
    english_only: bool

    async def score(self, query: str, documents: list[str]) -> list[float]:
        """Relevance of each document to the query, in [0, 1]."""
        ...


class NoReranker:
    name = "off"
    enabled = False
    english_only = False

    async def score(self, query: str, documents: list[str]) -> list[float]:
        return [0.0] * len(documents)


class QwenReranker:
    enabled = True
    english_only = False  # multilingual

    def __init__(
        self,
        model: str = "Qwen/Qwen3-Reranker-0.6B",
        device: str = "cpu",
        max_length: int = 1024,
        batch_size: int = 8,
    ):
        self.model_name = model
        self.name = f"qwen:{model.rsplit('/', 1)[-1]}"
        self.device = device
        self.max_length = max_length
        self.batch_size = batch_size
        self._model: Any = None
        self._tokenizer: Any = None
        self._load_lock = threading.Lock()
        # One scoring call at a time: concurrent batches would multiply peak memory.
        self._score_lock = asyncio.Lock()

    def load(self) -> None:
        """Load the model (from the Hugging Face cache). Safe to call repeatedly."""
        with self._load_lock:
            if self._model is not None:
                return
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer

            tokenizer = AutoTokenizer.from_pretrained(self.model_name, padding_side="left")
            # bf16 halves memory on GPUs; on CPU float32 is faster unless the CPU has bf16 units.
            dtype = torch.bfloat16 if self.device != "cpu" else torch.float32
            model = AutoModelForCausalLM.from_pretrained(self.model_name, dtype=dtype).to(self.device).eval()
            self._yes = tokenizer.convert_tokens_to_ids("yes")
            self._no = tokenizer.convert_tokens_to_ids("no")
            self._prefix = tokenizer.encode(_PREFIX, add_special_tokens=False)
            self._suffix = tokenizer.encode(_SUFFIX, add_special_tokens=False)
            self._tokenizer, self._model = tokenizer, model

    def _score_sync(self, query: str, documents: list[str]) -> list[float]:
        import torch

        self.load()
        tokenizer, model = self._tokenizer, self._model
        room = self.max_length - len(self._prefix) - len(self._suffix)
        scores: list[float] = []
        for start in range(0, len(documents), self.batch_size):
            pairs = [
                f"<Instruct>: {INSTRUCTION}\n<Query>: {query}\n<Document>: {doc}"
                for doc in documents[start : start + self.batch_size]
            ]
            encoded = tokenizer(
                pairs, padding=False, truncation="longest_first", max_length=room, return_attention_mask=False
            )
            encoded["input_ids"] = [self._prefix + ids + self._suffix for ids in encoded["input_ids"]]
            batch = tokenizer.pad(encoded, padding=True, return_tensors="pt").to(self.device)
            with torch.inference_mode():
                # Only the final position is needed: skip the vocabulary projection elsewhere.
                logits = model(**batch, logits_to_keep=1).logits[:, -1, :]
            pair = torch.stack([logits[:, self._no], logits[:, self._yes]], dim=1).float()
            scores.extend(torch.softmax(pair, dim=1)[:, 1].tolist())
        return scores

    async def score(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        async with self._score_lock:
            return await asyncio.to_thread(self._score_sync, query, documents)


class CrossEncoderReranker:
    """A BERT-style cross-encoder: (query, passage) in, one relevance logit out."""

    enabled = True
    english_only = True  # ms-marco MiniLM

    def __init__(
        self,
        model: str = "cross-encoder/ms-marco-MiniLM-L6-v2",
        device: str = "cpu",
        max_length: int = 512,
        batch_size: int = 16,
    ):
        self.model_name = model
        self.name = f"cross-encoder:{model.rsplit('/', 1)[-1]}"
        self.device = device
        self.max_length = max_length
        self.batch_size = batch_size
        self._model: Any = None
        self._tokenizer: Any = None
        self._load_lock = threading.Lock()
        self._score_lock = asyncio.Lock()

    def load(self) -> None:
        with self._load_lock:
            if self._model is not None:
                return
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            model = AutoModelForSequenceClassification.from_pretrained(self.model_name)
            self._tokenizer, self._model = tokenizer, model.to(self.device).eval()

    def _score_sync(self, query: str, documents: list[str]) -> list[float]:
        import torch

        self.load()
        scores: list[float] = []
        for start in range(0, len(documents), self.batch_size):
            docs = documents[start : start + self.batch_size]
            batch = self._tokenizer(
                [query] * len(docs),
                docs,
                padding=True,
                truncation="only_second",
                max_length=self.max_length,
                return_tensors="pt",
            ).to(self.device)
            with torch.inference_mode():
                logits = self._model(**batch).logits.float()
            # One logit: relevance (sigmoid to [0, 1]). Two: [irrelevant, relevant] classes.
            probs = torch.sigmoid(logits[:, 0]) if logits.shape[-1] == 1 else torch.softmax(logits, -1)[:, 1]
            scores.extend(probs.tolist())
        return scores

    async def score(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        async with self._score_lock:
            return await asyncio.to_thread(self._score_sync, query, documents)


DEFAULT_MODELS = {"cross-encoder": "cross-encoder/ms-marco-MiniLM-L6-v2", "qwen": "Qwen/Qwen3-Reranker-0.6B"}


def make_reranker(kind: str, model: str = "", device: str = "cpu") -> Reranker:
    """Construct (without loading) a reranker of `kind`: off | cross-encoder | qwen."""
    if kind == "off":
        return NoReranker()
    model = model or DEFAULT_MODELS[kind]
    return QwenReranker(model, device) if kind == "qwen" else CrossEncoderReranker(model, device)


def build_reranker(kind: str, model: str, device: str) -> Reranker:
    """The configured reranker, loaded, or NoReranker (with a warning) if its model isn't cached."""
    reranker = make_reranker(kind, model, device)
    if not reranker.enabled:
        return reranker
    try:
        reranker.load()  # type: ignore[attr-defined]
    except Exception as exc:  # missing from the cache, offline, out of memory...
        logger.warning(
            "Reranker %s unavailable (%s); results keep their fused order. "
            "Fetch it with: uv run python -m app.cli download-reranker",
            model,
            exc,
        )
        return NoReranker()
    return reranker
