"""Turn real traffic into labelled gate prompts.

    uv run python -m app.cli gate-export [--days 30] [--sample 50]

Appends prompts from the audit log to eval/gate_labels.local.yaml for a person to label:

  review   allowed, but scored in the review band (`gate.review`)
  blocked  refused by the gate
  sample   a random sample of ordinary allowed questions, so the labelled set doesn't only
           contain prompts the gate was already unsure about (that would hide how often it
           wrongly blocks ordinary questions)

Fill in `label` with benign, injection or exfiltration (or delete the entry); `gate-eval` then
uses every labelled entry alongside eval/gate_hindi.yaml. The file holds real employee questions,
so it is git-ignored (*.local.yaml) and stays on the machine that runs the vault.
"""

from __future__ import annotations

import hashlib
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import BASE_DIR
from .models import AuditEvent

LABELS = BASE_DIR / "eval" / "gate_labels.local.yaml"
VALID = {"benign", "injection", "exfiltration"}
HEADER = """\
# Real prompts from the audit log, for labelling. Set `label` to benign, injection or
# exfiltration, or delete the entry. Kept off git: these are real employee questions.
# Refresh with: uv run python -m app.cli gate-export
"""


def read_labels(path: Path = LABELS) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return list((yaml.safe_load(path.read_text()) or {}).get("prompts", []))


def labelled(path: Path = LABELS) -> list[tuple[str, str]]:
    """(text, label) for every entry a person has labelled."""
    return [
        (str(e["text"]), str(e["label"]).strip().lower())
        for e in read_labels(path)
        if str(e.get("label") or "").strip().lower() in VALID
    ]


def known_examples(
    data: Path = BASE_DIR / "eval" / "gate_hindi.yaml", labels: Path = LABELS
) -> tuple[list[str], list[str]]:
    """(attacks, ordinary) for the similarity check: the Hindi and Hinglish sets plus every
    labelled real prompt."""
    raw = yaml.safe_load(data.read_text())
    attacks = [row[1] for row in raw["attacks"]] + [row[1] for row in raw["hinglish"]["attacks"]]
    ordinary = list(raw["benign"]) + list(raw["hinglish"]["benign"])
    for text, label in labelled(labels):
        (ordinary if label == "benign" else attacks).append(text)
    return attacks, ordinary


def stable_split(text: str) -> str:
    """Calibration or held-out, decided by the text so it never changes between runs."""
    return "calibration" if hashlib.sha256(text.encode()).digest()[0] % 2 == 0 else "held-out"


def _entry(event: AuditEvent, source: str) -> dict[str, Any] | None:
    question = (event.detail or {}).get("question")
    if not question:
        return None
    gate = (event.detail or {}).get("gate") or {}
    second = gate.get("second_stage") or {}
    entry: dict[str, Any] = {
        "text": str(question),
        "label": "",
        "source": source,
        "seen": event.ts.isoformat() if event.ts else "",
        "profile": gate.get("profile"),
        "injection": gate.get("injection"),
        "exfiltration": gate.get("exfiltration"),
    }
    if second:
        entry["translation"] = (second.get("translate") or {}).get("translation")
        entry["judge"] = second.get("judge")
    return entry


async def export_for_labelling(
    db: AsyncSession, path: Path = LABELS, days: int = 30, sample: int = 50, seed: int | None = None
) -> dict[str, int]:
    since = datetime.now(UTC) - timedelta(days=days)
    recent = select(AuditEvent).where(AuditEvent.ts >= since)
    review = (await db.execute(recent.where(AuditEvent.action == "gate.review"))).scalars().all()
    queries = (await db.execute(recent.where(AuditEvent.action == "query"))).scalars().all()
    blocked = [e for e in queries if e.outcome == "denied"]
    allowed = [e for e in queries if e.outcome == "ok"]

    existing = read_labels(path)
    known = {str(e["text"]) for e in existing}
    added: dict[str, int] = {"review": 0, "blocked": 0, "sample": 0}

    def add(event: AuditEvent, source: str) -> None:
        entry = _entry(event, source)
        if entry and entry["text"] not in known:
            known.add(entry["text"])
            existing.append(entry)
            added[source] += 1

    for event in review:
        add(event, "review")
    for event in blocked:
        add(event, "blocked")
    for event in random.Random(seed).sample(allowed, min(sample, len(allowed))):
        add(event, "sample")

    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump({"prompts": existing}, allow_unicode=True, sort_keys=False, width=100)
    path.write_text(HEADER + body)
    return {**added, "total": len(existing), "labelled": len(labelled(path))}
