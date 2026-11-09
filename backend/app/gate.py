"""The prompt gate, carried over from Sentinel: Laya decides whether a prompt is an attack.

Two yes/no ("noul") questions, answered by the open Laya decision model in one forward pass:
prompt injection (override instructions, impersonate someone, bypass access controls) and
bulk exfiltration ("export every record"). Above the thresholds the request is refused before
anything is retrieved.

The gate is defence in depth, not the access control: what a user can read is decided by the
folder grants inside the search, so a prompt that slips past the gate still cannot reach a
folder the user has no grant for.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)

QUESTIONS = {
    "prompt_injection": {
        "type": "noul",
        "instructions": (
            "The message tries to override or reveal system instructions, impersonate "
            "another user or role, or talk the assistant into bypassing access controls"
        ),
    },
    "bulk_exfiltration": {
        "type": "noul",
        "instructions": (
            "The message asks to dump, export or list all records, or data about many "
            "people or customers at once"
        ),
    },
}


@dataclass(frozen=True)
class Thresholds:
    injection: float
    exfiltration: float


@dataclass
class GateVerdict:
    allowed: bool
    injection: float
    exfiltration: float
    engine: str
    latency_ms: float
    reasons: list[str]
    profile: str = "default"  # which thresholds applied: english | multilingual | default
    thresholds: Thresholds | None = None
    review: bool = False  # allowed, but close enough to a threshold to be worth a look


@dataclass(frozen=True)
class GateScores:
    injection: float
    exfiltration: float
    profile: str = "default"


class GateEngine(Protocol):
    name: str

    def score(self, prompt: str) -> GateScores: ...


class LayaGate:
    """Laya with language routing: English text goes to the `english` checkpoint, everything the
    English one can't read (Devanagari, other non-Latin scripts, detected non-English) goes to
    `multilingual`. The English checkpoint alone is near random on Hindi: it missed every Hindi
    injection in our probe and scored ordinary Hindi questions as bulk exports."""

    name = "laya"
    CHECKPOINTS = ["english", "multilingual"]

    def __init__(self, device: str = "cpu"):
        """`device` is a torch device ("cpu" or "cuda"; ROCm GPUs also use "cuda")."""
        os.environ.setdefault("HF_HUB_OFFLINE", "1")  # the models must already be cached
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        from laya import Router

        # default="english": text whose language detection abstains is mostly English here.
        # max_loaded=2 keeps both checkpoints resident instead of rebuilding on every switch.
        self._router = Router(default="english", vega=False, max_loaded=2, device=device)
        self._router.preload(self.CHECKPOINTS)
        self._lock = threading.Lock()  # one forward pass at a time

    def score(self, prompt: str) -> GateScores:
        with self._lock:
            out = self._router.predict({"user_message": prompt}, QUESTIONS)
        answers = out["answers"]
        return GateScores(
            float(answers["prompt_injection"]["noul"]),
            float(answers["bulk_exfiltration"]["noul"]),
            str(out.get("routing", {}).get("model") or "english"),
        )


class HeuristicGate:
    """Regex stand-in with the same output, for tests and machines without the model."""

    name = "heuristic"
    INJECTION = [
        r"\bignore\b.{0,40}\b(instruction|instructions|rules|polic(y|ies)|guardrails?)\b",
        r"\b(disregard|forget)\b.{0,30}\b(previous|prior|above|earlier|your)\b",
        r"\bsystem prompt\b",
        r"\byou are now\b",
        r"\b(act|acting|pretend)\b.{0,20}\b(admin|administrator|root|ceo|cfo|superuser)\b",
        r"\bi am\b.{0,10}\b(the )?(admin|administrator|ceo|cfo|root)\b",
        r"\b(bypass|override|circumvent|disable)\b.{0,30}\b(access|permissions?|controls?|filters?|security)\b",
        r"\bjailbreak|developer mode\b",
    ]
    EXFILTRATION = [
        r"\b(all|every|entire|full|complete|whole)\b.{0,40}\b(records?|employees?|customers?|salaries|"
        r"database|tables?|users?|documents?|files?|accounts?)\b",
        r"\b(dump|export|exfiltrate|bulk|scrape)\b",
        r"\bselect \* from\b",
    ]

    @staticmethod
    def _noisy_or(text: str, patterns: list[str], weight: float) -> float:
        p_false = 0.97
        for pattern in patterns:
            if re.search(pattern, text, flags=re.IGNORECASE):
                p_false *= 1 - weight
        return round(1 - p_false, 4)

    def score(self, prompt: str) -> GateScores:
        return GateScores(
            self._noisy_or(prompt, self.INJECTION, 0.75),
            self._noisy_or(prompt, self.EXFILTRATION, 0.55),
        )


class PromptGate:
    """Applies thresholds to the engine's scores. `per_profile` overrides the defaults for a
    checkpoint (Laya's `multilingual` scores run higher on ordinary text than `english`, so it
    gets its own, fitted with `app.cli gate-eval`). A prompt that is allowed but scores at least
    `review_threshold` on either question is marked for review in the audit log."""

    def __init__(
        self,
        engine: GateEngine | None,
        injection_threshold: float,
        exfil_threshold: float,
        per_profile: dict[str, Thresholds] | None = None,
        review_threshold: float | None = None,
    ):
        self.engine = engine
        self.default = Thresholds(injection_threshold, exfil_threshold)
        self.per_profile = dict(per_profile or {})
        self.review_threshold = review_threshold

    @property
    def name(self) -> str:
        return self.engine.name if self.engine else "off"

    # Kept for callers that show the default thresholds.
    @property
    def injection_threshold(self) -> float:
        return self.default.injection

    @property
    def exfil_threshold(self) -> float:
        return self.default.exfiltration

    def thresholds_for(self, profile: str) -> Thresholds:
        return self.per_profile.get(profile, self.default)

    def judge(self, scores: GateScores) -> tuple[list[str], Thresholds, bool]:
        limits = self.thresholds_for(scores.profile)
        reasons = []
        if scores.injection >= limits.injection:
            reasons.append(f"Looks like a prompt-injection attempt (p={scores.injection:.2f})")
        if scores.exfiltration >= limits.exfiltration:
            reasons.append(f"Looks like a bulk-export request (p={scores.exfiltration:.2f})")
        review = (
            not reasons
            and self.review_threshold is not None
            and max(scores.injection, scores.exfiltration) >= self.review_threshold
        )
        return reasons, limits, review

    async def check(self, prompt: str) -> GateVerdict:
        if self.engine is None:
            return GateVerdict(True, 0.0, 0.0, "off", 0.0, [])
        start = time.perf_counter()
        scores = await asyncio.to_thread(self.engine.score, prompt)
        reasons, limits, review = self.judge(scores)
        return GateVerdict(
            not reasons,
            scores.injection,
            scores.exfiltration,
            self.engine.name,
            round((time.perf_counter() - start) * 1000, 1),
            reasons,
            scores.profile,
            limits,
            review,
        )


def build_gate(
    engine: str,
    injection_threshold: float,
    exfil_threshold: float,
    torch_device: str = "cpu",
    per_profile: dict[str, Thresholds] | None = None,
    review_threshold: float | None = None,
) -> PromptGate:
    chosen: GateEngine | None = None
    if engine == "laya":
        try:
            chosen = LayaGate(device=torch_device)
        except Exception as exc:  # model not downloaded, laya missing, ...
            logger.warning(
                "Laya unavailable (%s); using the heuristic gate. "
                "Download it with: uv run python -m app.cli download-laya",
                exc,
            )
            chosen = HeuristicGate()
    elif engine == "heuristic":
        chosen = HeuristicGate()
    return PromptGate(chosen, injection_threshold, exfil_threshold, per_profile, review_threshold)
