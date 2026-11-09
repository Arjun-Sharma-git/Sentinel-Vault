"""Score retrieval and answers against a golden question set (eval/golden.yaml).

Retrieval, for each reranker setting:
  hit@1, hit@3, hit@k   share of answerable questions with an expected document at rank <= n
  MRR                   mean of 1 / rank of the first expected document (0 if not in the top k)
  latency               rerank time per question (mean and p95)

Answers (--answers; uses the configured LLM, slow on a CPU):
  correct               answerable questions whose answer contains every expected fact and
                        cites an expected document
  false refusals        answerable questions answered with "I couldn't find this"
  correct refusals      unanswerable questions (for that persona) answered with a refusal

Runs against the configured services (Postgres, OpenFGA, Qdrant, Ollama) through the same
Retriever and answer code as the API, but writes no audit records.
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select

from . import grounding
from .answer import LlmError, build_messages, extractive_answer, verify
from .config import BASE_DIR
from .devices import resolve_device
from .models import User
from .rerank import make_reranker
from .retrieval import Passage
from .services import Services

GOLDEN = BASE_DIR / "eval" / "golden.yaml"
EMAIL_DOMAIN = "acme.example.com"


@dataclass
class Question:
    ask: str
    persona: str
    expect: list[str] = field(default_factory=list)
    facts: list[str | list[str]] = field(default_factory=list)
    answerable: bool = True


def load_questions(path: Path = GOLDEN) -> list[Question]:
    raw = yaml.safe_load(path.read_text())["questions"]
    return [
        Question(q["ask"], q["as"], q.get("expect", []), q.get("facts", []), q.get("answerable", True))
        for q in raw
    ]


def first_hit(passages: list[Passage], expect: list[str]) -> int | None:
    """1-based rank of the first passage from an expected document."""
    return next((i for i, p in enumerate(passages, start=1) if p.rel_path in expect), None)


def facts_present(text: str, facts: list[str | list[str]]) -> list[str]:
    """The facts missing from `text` (each fact may be a list of acceptable spellings)."""
    lowered = text.lower()
    missing = []
    for fact in facts:
        options = fact if isinstance(fact, list) else [fact]
        if not any(str(o).lower() in lowered for o in options):
            missing.append(" | ".join(map(str, options)))
    return missing


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]


async def _user_ids(services: Services, personas: set[str]) -> dict[str, str]:
    async with services.sessions() as db:
        rows = await db.execute(
            select(User.email, User.id).where(User.email.in_([f"{p}@{EMAIL_DOMAIN}" for p in personas]))
        )
        found = {email.split("@")[0]: uid for email, uid in rows.all()}
    missing = personas - set(found)
    if missing:
        raise SystemExit(f"Unknown personas {sorted(missing)}: run `python -m app.cli seed-demo` first.")
    return found


async def evaluate(
    services: Services,
    rerankers: list[str],
    answers: bool = False,
    questions: list[Question] | None = None,
    within: set[str] | None = None,
) -> dict[str, Any]:
    """`within`: search only these folder ids (a source's own folders); None searches everything."""
    questions = questions or load_questions()
    users = await _user_ids(services, {q.persona for q in questions})
    retriever = services.retriever
    original = retriever.reranker
    device = resolve_device(services.settings.torch_device).torch_device
    top_k = retriever.top_k
    report: dict[str, Any] = {"top_k": top_k, "questions": len(questions), "configs": {}}

    for kind in rerankers:
        # The configured model for the configured kind; each other kind uses its default model.
        model = services.settings.reranker_model if kind == services.settings.reranker else ""
        if kind == services.settings.reranker and original.enabled:
            reranker = original  # already loaded by the app: don't put a second copy on the GPU
        else:
            reranker = make_reranker(kind, model, device)
            if reranker.enabled:
                try:
                    reranker.load()  # type: ignore[attr-defined]
                except Exception as exc:  # not cached, offline, out of memory...
                    report["configs"][kind] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
                    continue
        retriever.reranker = reranker
        rows: list[dict[str, Any]] = []
        for q in questions:
            async with services.sessions() as db:
                started = time.perf_counter()
                result = await retriever.retrieve(db, users[q.persona], q.ask, within)
                retrieve_ms = (time.perf_counter() - started) * 1000
            passages = result.passages
            row: dict[str, Any] = {
                "ask": q.ask,
                "as": q.persona,
                "answerable": q.answerable,
                "rank": first_hit(passages, q.expect) if q.answerable else None,
                "top": [p.rel_path for p in passages[:3]],
                "retrieve_ms": round(retrieve_ms, 1),
                "rerank_ms": result.timings_ms.get("rerank_ms"),
            }
            if answers:
                row.update(await _answer(services, q, passages))
            rows.append(row)
        report["configs"][reranker.name if reranker.enabled else "off"] = {
            "summary": summarize(rows, top_k, answers),
            "rows": rows,
        }
    retriever.reranker = original
    return report


async def _answer(services: Services, q: Question, passages: list[Passage]) -> dict[str, Any]:
    if not passages:
        return {
            "refused": True,
            "missing": [],
            "cited_expected": False,
            "answer_ms": 0.0,
            "faithfulness": None,
        }
    started = time.perf_counter()
    raw = ""
    if services.llm is not None:
        try:
            messages = build_messages(q.ask, passages, services.settings.datamark_sources)
            raw = "".join([piece async for piece in services.llm.stream(messages)])
        except LlmError:
            raw = ""
    raw = raw or extractive_answer(passages)
    verified = verify(raw, len(passages))
    refused = verified.no_answer or not verified.cited
    cited = {passages[n - 1].rel_path for n in verified.cited}
    faithfulness = faithfulness_llm = agreement = None
    if not refused and services.llm is not None and services.settings.grounding_check:
        judged = await grounding.check(services.llm, verified.text, passages, services.fact_checker)
        faithfulness = judged.faithfulness
        if services.fact_checker is not None:
            # Same answer, answer-model judge: to compare the two judges claim by claim.
            baseline = await grounding.check(services.llm, verified.text, passages)
            faithfulness_llm = baseline.faithfulness
            pairs = [
                (a.supported, b.supported)
                for a, b in zip(judged.claims, baseline.claims, strict=True)
                if a.supported is not None and b.supported is not None
            ]
            agreement = round(sum(a == b for a, b in pairs) / len(pairs), 3) if pairs else None
    return {
        "faithfulness": faithfulness,
        "faithfulness_llm": faithfulness_llm,
        "judge_agreement": agreement,
        "refused": refused,
        "missing": [] if refused else facts_present(verified.text, q.facts),
        "cited_expected": bool(cited & set(q.expect)),
        "answer": verified.text[:400],
        "answer_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def _mean(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [r[key] for r in rows if r.get(key) is not None]
    return round(statistics.mean(values), 3) if values else None


def summarize(rows: list[dict[str, Any]], top_k: int, answers: bool) -> dict[str, Any]:
    answerable = [r for r in rows if r["answerable"]]
    unanswerable = [r for r in rows if not r["answerable"]]
    n = len(answerable) or 1

    def hit(at: int) -> float:
        return round(sum(1 for r in answerable if r["rank"] and r["rank"] <= at) / n, 3)

    rerank = [r["rerank_ms"] for r in rows if r["rerank_ms"] is not None]
    summary: dict[str, Any] = {
        "hit@1": hit(1),
        "hit@3": hit(3),
        f"hit@{top_k}": hit(top_k),
        "mrr": round(sum(1 / r["rank"] for r in answerable if r["rank"]) / n, 3),
        "rerank_ms_mean": round(statistics.mean(rerank), 1) if rerank else 0.0,
        "rerank_ms_p95": round(_p95(rerank), 1),
        "retrieve_ms_mean": round(statistics.mean(r["retrieve_ms"] for r in rows), 1),
    }
    if answers:
        summary.update(
            {
                "correct": round(
                    sum(
                        1 for r in answerable if not r["refused"] and not r["missing"] and r["cited_expected"]
                    )
                    / n,
                    3,
                ),
                "false_refusals": round(sum(1 for r in answerable if r["refused"]) / n, 3),
                "correct_refusals": round(
                    sum(1 for r in unanswerable if r["refused"]) / (len(unanswerable) or 1), 3
                ),
                # RAGAS faithfulness: share of an answer's claims its cited sources support, averaged
                "faithfulness": _mean(rows, "faithfulness"),
                "answer_ms_mean": round(statistics.mean(r["answer_ms"] for r in rows), 1),
            }
        )
        if any(r.get("faithfulness_llm") is not None for r in rows):  # MiniCheck run: compare judges
            summary["faithfulness_llm_judge"] = _mean(rows, "faithfulness_llm")
            summary["judge_agreement"] = _mean(rows, "judge_agreement")
    return summary


def render(report: dict[str, Any]) -> str:
    """A Markdown table of the summaries, plus the misses of each config."""
    configs = {k: v for k, v in report["configs"].items() if "summary" in v}
    if not configs:
        return "No configuration ran."
    columns = list(next(iter(configs.values()))["summary"])
    lines = [
        f"{report['questions']} questions, top {report['top_k']} passages\n",
        "| reranker | " + " | ".join(columns) + " |",
        "|---|" + "---|" * len(columns),
    ]
    for name, config in configs.items():
        lines.append(f"| {name} | " + " | ".join(str(config["summary"][c]) for c in columns) + " |")
    for name, config in configs.items():
        misses = [r for r in config["rows"] if r["answerable"] and r["rank"] != 1]
        if misses:
            lines.append(f"\nNot ranked first with {name}:")
            lines += [
                f"  - [{r['as']}] {r['ask']}  (rank {r['rank'] or '-'}; top: {r['top'][:1]})" for r in misses
            ]
        wrong = [r for r in config["rows"] if r.get("missing") or (r["answerable"] and r.get("refused"))]
        wrong += [r for r in config["rows"] if not r["answerable"] and r.get("refused") is False]
        if wrong:
            lines.append(f"\nAnswer problems with {name}:")
            for r in wrong:
                issue = (
                    "answered an unanswerable question"
                    if not r["answerable"]
                    else "refused"
                    if r["refused"]
                    else f"missing {r['missing']}"
                )
                lines.append(f"  - [{r['as']}] {r['ask']}: {issue}")
    return "\n".join(lines)


def save(report: dict[str, Any], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"eval-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=2, default=str))
    return path
