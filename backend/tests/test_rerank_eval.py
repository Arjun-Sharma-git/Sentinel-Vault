"""Reranking (order only, never permissions) and the evaluation harness."""

from __future__ import annotations

from pathlib import Path

from app.cli import DEMO_USERS
from app.evaluation import facts_present, first_hit, load_questions, summarize
from app.retrieval import Passage, Retriever, mostly_non_latin

LIBRARY = Path(__file__).resolve().parents[2] / "data" / "library"


class RecordingReranker:
    """Scores 1.0 for passages containing `favorite`, else 0.0, and records what it read."""

    name = "recording"
    enabled = True

    def __init__(self, favorite: str):
        self.favorite = favorite.lower()
        self.seen: list[str] = []

    async def score(self, query: str, documents: list[str]) -> list[float]:
        self.seen += documents
        return [1.0 if self.favorite in d.lower() else 0.0 for d in documents]


def _retrieve(api, who: str, question: str, reranker) -> list[Passage]:
    services = api.client.app.state.services
    base = services.retriever
    retriever = Retriever(base.authz, base.vectors, base.embedder, base.candidates, base.top_k, reranker, 12)
    user_id = api.user_id(who)  # an HTTP call: resolve it outside the event loop

    async def run():
        async with services.sessions() as db:
            return (await retriever.retrieve(db, user_id, question)).passages

    return api.client.portal.call(run)


def test_reranker_changes_the_order(api):
    reranker = RecordingReranker(favorite="Laptops are replaced")
    passages = _retrieve(api, "alice", "laptop wifi passwords travel", reranker)
    assert passages[0].rel_path == "general/it-help.md"
    assert passages[0].rerank_score == 1.0
    assert all(p.rerank_score is not None for p in passages)


def test_reranker_only_reads_permitted_passages(api):
    """Reranking happens after the permission re-check: hidden text never reaches it."""
    reranker = RecordingReranker(favorite="TEAL-HERON-58")
    passages = _retrieve(api, "dave", "payroll code word TEAL-HERON-58 salary band L5", reranker)
    assert reranker.seen  # it did run
    assert not any("TEAL-HERON-58" in text for text in reranker.seen)
    assert not any(p.folder_path.startswith("hr") for p in passages)


def test_english_only_rerankers_skip_hindi_questions(api):
    english_only = RecordingReranker(favorite="Laptops are replaced")
    english_only.english_only = True
    _retrieve(api, "alice", "मेरा लैपटॉप चोरी हो गया, रिपोर्ट कब करूँ?", english_only)
    assert english_only.seen == []  # fused order kept
    _retrieve(api, "alice", "laptop wifi passwords travel", english_only)
    assert english_only.seen  # English still reranked


def test_script_detection():
    assert mostly_non_latin("पूर्णकालिक कर्मचारियों को कितनी छुट्टी मिलती है?")
    assert mostly_non_latin("PayGate को साल में कितना भुगतान करते हैं?")  # Latin names inside Hindi
    assert not mostly_non_latin("How much do we pay PayGate per year?")
    assert not mostly_non_latin("kya main bachi hui chhuttiyan le ja sakta hoon?")  # Hinglish
    assert not mostly_non_latin("1234 ?")


# ---- The harness ----------------------------------------------------------------------
def test_golden_set_is_consistent():
    questions = load_questions()
    personas = {email.split("@")[0] for email, *_ in DEMO_USERS}
    assert len(questions) >= 40
    for q in questions:
        assert q.persona in personas, q.ask
        if q.answerable:
            assert q.expect and q.facts, q.ask
            for path in q.expect:
                assert (LIBRARY / path).is_file(), path
        else:
            assert not q.expect, q.ask


def _passage(rel_path: str) -> Passage:
    return Passage("c", "d", "t", "f", "folder", rel_path, "", "text", 0.0)


def test_metrics():
    passages = [_passage("a.md"), _passage("b.md"), _passage("c.md")]
    assert first_hit(passages, ["b.md"]) == 2
    assert first_hit(passages, ["z.md"]) is None
    assert facts_present("Revenue was 48.2 million", ["48.2", ["million", "M"]]) == []
    assert facts_present("Revenue was 48.2", [["three", "3"]]) == ["three | 3"]
    rows = [
        {"answerable": True, "rank": 1, "rerank_ms": 10.0, "retrieve_ms": 20.0},
        {"answerable": True, "rank": 3, "rerank_ms": 30.0, "retrieve_ms": 40.0},
        {"answerable": True, "rank": None, "rerank_ms": None, "retrieve_ms": 10.0},
        {"answerable": False, "rank": None, "rerank_ms": None, "retrieve_ms": 10.0},
    ]
    summary = summarize(rows, top_k=6, answers=False)
    assert summary["hit@1"] == round(1 / 3, 3)
    assert summary["hit@3"] == round(2 / 3, 3)
    assert summary["mrr"] == round((1 + 1 / 3) / 3, 3)
