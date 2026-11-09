"""Calibrate and test the prompt gate on eval/gate_hindi.yaml (plus the English golden questions).

    uv run python -m app.cli gate-eval [--budget 0.02]

Every prompt is scored once. For each Laya checkpoint that the prompts routed to, thresholds are
fitted on the calibration half (even positions): the pair that blocks the most attacks while
wrongly blocking at most `budget` of the ordinary questions. They are then reported on the held-out
half (odd positions), which they were not fitted to. With ~75 ordinary questions per half, a 2%
budget means at most one wrongly blocked question, so read the numbers as a sanity check, not a
guarantee: thresholds fitted this way are only as good as the prompts behind them.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import BASE_DIR
from .evaluation import load_questions
from .gate import GateEngine, GateScores, PromptGate, SecondOpinionEngine, Thresholds

DATA = BASE_DIR / "eval" / "gate_hindi.yaml"
GRID = [round(0.30 + 0.01 * i, 2) for i in range(70)]  # 0.30 .. 0.99
MIN_EACH = 20  # ordinary prompts and attacks a checkpoint needs in the calibration half to be fitted


@dataclass
class Prompt:
    text: str
    set: str  # hindi | hinglish | english
    label: str  # benign | injection | exfiltration
    split: str  # calibration | held-out
    scores: GateScores | None = None
    final: bool | None = None  # blocked by the whole gate, second stage included
    second: dict[str, Any] | None = None

    @property
    def attack(self) -> bool:
        return self.label != "benign"


def load_prompts(path: Path = DATA) -> list[Prompt]:
    data = yaml.safe_load(path.read_text())
    prompts: list[Prompt] = []

    def add(rows: list[Any], set_name: str, attacks: bool) -> None:
        for i, row in enumerate(rows):
            label, text = (row[0], row[1]) if attacks else ("benign", row)
            prompts.append(Prompt(text, set_name, label, "calibration" if i % 2 == 0 else "held-out"))

    add(data["benign"], "hindi", False)
    add(data["attacks"], "hindi", True)
    add(data["hinglish"]["benign"], "hinglish", False)
    add(data["hinglish"]["attacks"], "hinglish", True)
    add([q.ask for q in load_questions()], "english", False)
    return prompts


def blocked(s: GateScores, t: Thresholds) -> bool:
    return s.injection >= t.injection or s.exfiltration >= t.exfiltration


def fit(prompts: list[Prompt], budget: float) -> Thresholds:
    """Most attacks blocked with at most `budget` of ordinary prompts blocked. Ties go to fewer
    ordinary prompts blocked, then to the more lenient pair (fewer surprises on unseen text)."""
    benign = [p for p in prompts if not p.attack]
    attacks = [p for p in prompts if p.attack]
    allowed_wrong = int(budget * len(benign) + 1e-9)
    best: tuple[int, int, float] | None = None
    choice = Thresholds(0.99, 0.99)
    for ti in GRID:
        for te in GRID:
            t = Thresholds(ti, te)
            wrong = sum(blocked(p.scores, t) for p in benign)
            if wrong > allowed_wrong:
                continue
            key = (sum(blocked(p.scores, t) for p in attacks), -wrong, ti + te)
            if best is None or key > best:
                best, choice = key, t
    return choice


def evaluate_gate(
    engine: GateEngine,
    budget: float,
    default: Thresholds,
    second: SecondOpinionEngine | None = None,
    scope: str = "review",
    review_threshold: float | None = 0.5,
) -> dict[str, Any]:
    prompts = load_prompts()
    start = time.perf_counter()
    for p in prompts:
        p.scores = engine.score(p.text)
    ms = (time.perf_counter() - start) * 1000 / len(prompts)

    profiles = sorted({p.scores.profile for p in prompts})
    report: dict[str, Any] = {"engine": engine.name, "budget": budget, "ms_per_prompt": round(ms, 1)}
    fitted: dict[str, Thresholds] = {}
    for profile in profiles:
        mine = [p for p in prompts if p.scores.profile == profile]
        calibration = [p for p in mine if p.split == "calibration"]
        # Fit only where there is enough of both kinds; a handful of prompts just overfits.
        attacks = sum(p.attack for p in calibration)
        enough = attacks >= MIN_EACH and len(calibration) - attacks >= MIN_EACH
        fitted[profile] = fit(calibration, budget) if enough else default
        report.setdefault("thresholds", {})[profile] = {**asdict(fitted[profile]), "fitted": enough}

    def by_set(is_blocked) -> dict[str, Any]:
        out = {}
        for set_name in ("hindi", "hinglish", "english"):
            for split in ("calibration", "held-out"):
                rows = [p for p in prompts if p.set == set_name and p.split == split]
                wrong = [p for p in rows if not p.attack and is_blocked(p)]
                caught = [p for p in rows if p.attack and is_blocked(p)]
                out[f"{set_name}/{split}"] = {
                    "ordinary": sum(not p.attack for p in rows),
                    "ordinary_blocked": len(wrong),
                    "attacks": sum(p.attack for p in rows),
                    "attacks_blocked": len(caught),
                    "wrongly_blocked": [p.text for p in wrong],
                    "missed": [p.text for p in rows if p.attack and p not in caught],
                }
        return out

    report["before"] = by_set(lambda p: blocked(p.scores, default))
    report["after"] = by_set(lambda p: blocked(p.scores, fitted[p.scores.profile]))
    if second is not None:
        # The whole gate as it runs in the app: fitted thresholds, then the second stage.
        gate = PromptGate(
            engine,
            default.injection,
            default.exfiltration,
            {k: v for k, v in fitted.items() if k != "default"},
            review_threshold,
            second,
            scope,  # type: ignore[arg-type]
        )

        async def run() -> float:
            start = time.perf_counter()
            for p in prompts:
                verdict = await gate.check(p.text)
                p.final, p.second = not verdict.allowed, verdict.second_stage
            return time.perf_counter() - start

        seconds = asyncio.run(run())
        looked = [p for p in prompts if p.second is not None]
        report["second_stage"] = {
            "scope": scope,
            "share_of_hindi_prompts": round(
                sum(1 for p in looked if p.set == "hindi") / max(1, sum(p.set == "hindi" for p in prompts)), 3
            ),
            "looked_at": len(looked),
            "ms_mean_when_used": round(
                sum(p.second.get("latency_ms", 0) for p in looked) / max(1, len(looked)),
                1,  # type: ignore[union-attr]
            ),
            "seconds_total": round(seconds, 1),
        }
        report["cascade"] = by_set(lambda p: bool(p.final))
    report["routes"] = {
        s: sorted({p.scores.profile for p in prompts if p.set == s}) for s in ("hindi", "hinglish", "english")
    }
    report["scores"] = [
        {
            "text": p.text,
            "set": p.set,
            "label": p.label,
            "split": p.split,
            **asdict(p.scores),
            "blocked": p.final,
            "second_stage": p.second,
        }
        for p in prompts
    ]
    return report


def render_gate(report: dict[str, Any]) -> str:
    lines = [f"engine {report['engine']}, {report['ms_per_prompt']} ms/prompt, budget {report['budget']:.0%}"]
    for profile, t in report["thresholds"].items():
        how = "fitted on the calibration half" if t["fitted"] else "default (too few prompts here to fit)"
        lines.append(f"  {profile}: injection {t['injection']:.2f}, export {t['exfiltration']:.2f} ({how})")
    if "second_stage" in report:
        s = report["second_stage"]
        lines.append(
            f"  second stage ({s['scope']}): looked at {s['looked_at']} prompts, "
            f"{s['share_of_hindi_prompts']:.0%} of Hindi ones, {s['ms_mean_when_used']} ms each"
        )
    for stage in ("before", "after", "cascade"):
        if stage not in report:
            continue
        lines.append(f"\n{stage}: | set/split | ordinary blocked | attacks blocked |")
        for key, r in report[stage].items():
            ordinary = f"{r['ordinary_blocked']}/{r['ordinary']}"
            lines.append(f"  | {key} | {ordinary} | {r['attacks_blocked']}/{r['attacks']} |")
    final = report.get("cascade", report["after"])
    held = {k: v for k, v in final.items() if k.endswith("held-out")}
    for key, r in held.items():
        for text in r["wrongly_blocked"]:
            lines.append(f"  wrongly blocked [{key}]: {text}")
        for text in r["missed"]:
            lines.append(f"  missed [{key}]: {text}")
    return "\n".join(lines)


def save_gate(report: dict[str, Any], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"gate-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return path
