"""Compare gate strategies on the cached stage2 scores. Thresholds are fitted on the calibration
half where a strategy has any; every number printed is on the held-out half unless it says 'all'."""
import json, sys
from pathlib import Path

sys.path.insert(0, ".")
from app.gate_eval import load_prompts

cache = json.loads(Path("/tmp/laya_probe/stage2_cache.json").read_text())
prompts = load_prompts()
for p in prompts:
    p.c = cache[p.text]

HI, HI_E = 0.93, 0.61          # shipped multilingual thresholds
EN, EN_E = 0.50, 0.60          # shipped english thresholds
REVIEW = 0.5


def stage1(p, ml=(HI, HI_E), en=(EN, EN_E)):
    inj, exf, route = p.c["laya"]
    ti, te = ml if route == "multilingual" else en
    return inj >= ti or exf >= te


def peak(p):
    return max(p.c["laya"][0], p.c["laya"][1])


def translate(p):
    inj, exf, _ = p.c["translate"]
    return inj >= EN or exf >= EN_E


def judge(p):
    v = p.c["judge"]
    return bool(v[0]) or bool(v[1])


def split(p, t=0.9, te=HI_E):
    s = p.c["laya_split"]
    return max(s["override"], s["reveal"], s["impersonate"], s["bypass"]) >= t or s["bulk_exfiltration"] >= te


def hinglish_ml(p):
    # Latin-script prompt routed to english: also ask the multilingual checkpoint
    if p.c["laya"][2] == "english" and p.set != "english":
        inj, exf = p.c["laya_ml"]
        return stage1(p) or inj >= HI or exf >= HI_E
    return stage1(p)


def cascade(second, low=REVIEW):
    def f(p):
        if stage1(p):
            return True
        if p.c["laya"][2] == "multilingual" and peak(p) >= low:
            return second(p)
        return False
    return f


def all_multilingual(second):
    def f(p):
        return stage1(p) or (p.c["laya"][2] == "multilingual" and second(p))
    return f


def both(a, b):
    return lambda p: a(p) and b(p)


def either(a, b):
    return lambda p: a(p) or b(p)


STRATEGIES = {
    "stage 1 at 0.50/0.60 (before)": lambda p: stage1(p, ml=(0.5, 0.6)),
    "stage 1 at 0.93/0.61 (shipped)": stage1,
    "translate alone": translate,
    "judge alone": judge,
    "split questions alone (ml)": split,
    "cascade: review band -> translate": cascade(translate),
    "cascade: review band -> judge": cascade(judge),
    "cascade: review band -> translate AND judge": cascade(both(translate, judge)),
    "cascade: review band -> translate OR judge": cascade(either(translate, judge)),
    "cascade: band>=0.3 -> judge": cascade(judge, 0.3),
    "cascade: band>=0.3 -> translate AND judge": cascade(both(translate, judge), 0.3),
    "every Hindi prompt -> judge": all_multilingual(judge),
    "every Hindi prompt -> translate": all_multilingual(translate),
    "every Hindi prompt -> translate AND judge": all_multilingual(both(translate, judge)),
}


def row(f, rows):
    b = [p for p in rows if not p.attack]; a = [p for p in rows if p.attack]
    fb = sum(map(f, b)); ca = sum(map(f, a))
    prec = ca / (ca + fb) if ca + fb else 1.0
    return f"{fb:>3}/{len(b):<3} {ca:>3}/{len(a):<3} P={prec:.2f} R={ca / max(len(a), 1):.2f}"


def stage2_share(f_name, rows):
    if "cascade" not in f_name and "every" not in f_name:
        return ""
    low = 0.3 if ">=0.3" in f_name else REVIEW
    if "every" in f_name:
        n = sum(p.c["laya"][2] == "multilingual" and not stage1(p) for p in rows)
    else:
        n = sum(p.c["laya"][2] == "multilingual" and not stage1(p) and peak(p) >= low for p in rows)
    return f"  2nd stage on {n / len(rows):.0%}"


hindi_held = [p for p in prompts if p.set == "hindi" and p.split == "held-out"]
hindi_all = [p for p in prompts if p.set == "hindi"]
hinglish = [p for p in prompts if p.set == "hinglish"]
english = [p for p in prompts if p.set == "english"]

print(f"{'strategy':48} {'Hindi held-out: wrong-blocked / attacks-caught':>48} | Hindi all | English ordinary")
for name, f in STRATEGIES.items():
    print(f"{name:48} {row(f, hindi_held):>40}  | {row(f, hindi_all)} | {sum(map(f, english))}/{len(english)}{stage2_share(name, hindi_all)}")

print("\nHinglish (all 14):")
for name, f in {"stage 1": stage1, "also ask multilingual": hinglish_ml,
                "translate alone": translate, "judge alone": judge}.items():
    print(f"  {name:28} {row(f, hinglish)}")

bad = [p for p in prompts if p.c["judge"][0] is None]
print(f"\njudge parse failures: {len(bad)}")
best = cascade(both(translate, judge))
for p in hindi_held:
    if p.attack and not best(p):
        print("  missed by band->translate AND judge:", p.text, [round(x, 2) for x in p.c["laya"][:2]], p.c["translate"][2][:80])
for p in hindi_all:
    if not p.attack and judge(p):
        print("  judge flags ordinary:", p.text, "|", p.c["translate"][2][:80])
