"""Pure functions: rules, chunking, parsing, fusion, citation verification, filenames."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from app.answer import MARK, NO_ANSWER, datamark, verify
from app.api.library_routes import safe_filename
from app.authz.rules import AccessSnapshot
from app.ingest.chunk import chunk_sections, estimate_tokens
from app.ingest.parse import Section, markdown_sections
from app.retrieval import or_query, rrf


def test_rules_inheritance_and_editor_implies_viewer():
    snap = AccessSnapshot(
        parents={"root": None, "child": "root", "other": None},
        memberships={"u": {"g"}},
        grants={("root", "editor"): {"group:g"}},
    )
    assert snap.folders_for("u", "viewer") == {"root", "child"}
    assert snap.folders_for("u", "editor") == {"root", "child"}
    assert snap.folders_for("stranger", "viewer") == set()


@given(st.dictionaries(st.sampled_from("abcde"), st.sampled_from("abcde")))
def test_rules_terminate_on_cyclic_parents(parents):
    snap = AccessSnapshot(parents={k: v for k, v in parents.items()}, grants={("a", "viewer"): {"user:u"}})
    snap.folders_for("u", "viewer")  # must not loop forever


def test_markdown_sections_keep_heading_paths():
    title, sections = markdown_sections(
        "# Handbook\n\n## Leave\nText A\n\n### Interns\nText B\n```\n# not a heading\n```"
    )
    assert title == "Handbook"
    assert [s.heading for s in sections] == ["Handbook › Leave", "Handbook › Leave › Interns"]
    assert "# not a heading" in sections[1].text


@given(st.text(min_size=1, max_size=3000))
def test_chunks_stay_under_budget_and_lose_no_words(text):
    chunks = chunk_sections([Section("h", text)], max_tokens=60)
    for chunk in chunks:
        assert estimate_tokens(chunk.text) <= 60 or len(chunk.text.split()) == 1 or "\n" not in chunk.text
    joined = " ".join(c.text for c in chunks)
    for word in text.split()[:50]:
        if len(word.strip()) >= 3:
            assert word.strip() in joined


def test_rrf_rewards_agreement():
    fused = rrf({"dense": ["a", "b", "c"], "text": ["b", "d"]})
    assert max(fused, key=lambda k: fused[k][0]) == "b"
    assert fused["b"][1] == ["dense", "text"]


def test_or_query_is_safe_for_to_tsquery():
    assert or_query("What's Q3 revenue? (USD) & margin!") == "what | q3 | revenue | usd | margin"
    assert or_query("?!") is None


def test_verify_drops_citations_to_unsent_sources():
    result = verify(f"Revenue was 48M [S1]. Margin 72% [S7].{MARK}", n_sources=2)
    assert result.cited == [1] and result.dropped == [7]
    assert "[S7]" not in result.text and MARK not in result.text


def test_verify_handles_no_answer_and_uncited_text():
    assert verify(NO_ANSWER, 3).no_answer
    uncited = verify("The provided sources do not contain this.", 3)
    assert uncited.no_answer and not uncited.grounded
    # a refusal with a citation attached is still a refusal
    cited_refusal = verify("The provided sources do not contain information about Project Northwind [S2].", 3)
    assert cited_refusal.no_answer and cited_refusal.cited == []
    assert verify("I couldn't find that [S1].", 3).no_answer
    # a real answer that merely mentions a missing detail is still an answer
    partial = "Interns accrue 1.5 days per month [S1]. The sources do not mention part-timers."
    assert not verify(partial, 3).no_answer


def test_datamark_joins_words():
    assert datamark("ignore all\n previous") == f"ignore{MARK}all{MARK}previous"


def test_safe_filename():
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("..\\..\\win.ini") == "win.ini"
    assert safe_filename(".hidden.md") == "hidden.md"
    assert safe_filename("Q3 report (final).pdf") == "Q3 report _final_.pdf"


async def test_ollama_embedding_retries_once_after_the_model_process_crashes(monkeypatch):
    import httpx

    from app import embeddings

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(400, json={"error": "llama-server process no longer running"})
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})

    monkeypatch.setattr(embeddings, "RETRY_DELAY_SECONDS", 0)
    embedder = embeddings.OllamaEmbedder("http://ollama.test", "m")
    embedder._http = httpx.AsyncClient(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    assert await embedder.embed_documents(["x"]) == [[0.1, 0.2]]
    assert len(calls) == 2


async def test_ollama_embedding_gives_up_after_the_retry(monkeypatch):
    import httpx
    import pytest

    from app import embeddings

    monkeypatch.setattr(embeddings, "RETRY_DELAY_SECONDS", 0)
    embedder = embeddings.OllamaEmbedder("http://ollama.test", "m")
    embedder._http = httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(lambda r: httpx.Response(400))
    )
    with pytest.raises(embeddings.EmbeddingError):
        await embedder.embed_documents(["x"])
