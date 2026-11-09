import sys; sys.path.insert(0, ".")
src = open("../desktop-eval-logs/stage2_report.py").read().split("STRATEGIES = {")[0]
exec(src)
def row(f, rows):
    b = [p for p in rows if not p.attack]; a = [p for p in rows if p.attack]
    fb = sum(map(f, b)); ca = sum(map(f, a))
    return f"{fb:>3}/{len(b):<3} {ca:>3}/{len(a):<3} P={ca/(ca+fb) if ca+fb else 1:.2f} R={ca/max(len(a),1):.2f}"
hindi_held = [p for p in prompts if p.set == "hindi" and p.split == "held-out"]
hindi_all = [p for p in prompts if p.set == "hindi"]
hinglish = [p for p in prompts if p.set == "hinglish"]
english = [p for p in prompts if p.set == "english"]
INJ = ("override", "reveal", "impersonate", "bypass")

def tsplit_score(p):
    s = p.c["translate_split"]; return max(s[k] for k in INJ), s["bulk_exfiltration"]

def make_tsplit(ti, te):
    return lambda p: (lambda i, e: i >= ti or e >= te)(*tsplit_score(p))

# fit translate+split thresholds on the Hindi calibration half: most attacks with <=1 ordinary blocked
cal = [p for p in prompts if p.set == "hindi" and p.split == "calibration"]
grid = [round(0.3 + 0.01 * i, 2) for i in range(70)]
best = None
for ti in grid:
    for te in grid:
        f = make_tsplit(ti, te)
        w = sum(f(p) for p in cal if not p.attack)
        if w > 1: continue
        k = (sum(f(p) for p in cal if p.attack), -w, ti + te)
        if best is None or k > best[0]: best = (k, ti, te)
_, TI, TE = best
print(f"translate+split thresholds fitted on calibration: {TI}/{TE}")
tsplit = make_tsplit(TI, TE)
any2 = lambda *fs: (lambda p: any(f(p) for f in fs))

S = {
    "stage 1 shipped": stage1,
    "band -> translate": cascade(translate),
    "band -> translate OR judge": cascade(any2(translate, judge)),
    "band -> translate+split": cascade(tsplit),
    "band -> translate OR translate+split": cascade(any2(translate, tsplit)),
    "band -> translate OR translate+split OR judge": cascade(any2(translate, tsplit, judge)),
    "all Hindi -> translate": all_multilingual(translate),
    "all Hindi -> translate OR judge": all_multilingual(any2(translate, judge)),
    "all Hindi -> translate OR translate+split": all_multilingual(any2(translate, tsplit)),
    "all Hindi -> translate OR translate+split OR judge": all_multilingual(any2(translate, tsplit, judge)),
    "all Hindi -> judge only (replaces stage 1)": lambda p: judge(p) if p.c["laya"][2] == "multilingual" else stage1(p),
}
hc = [p for p in prompts if p.set == "hindi" and p.split == "calibration"]
print(f"{'strategy':52} {'calibration':>26} | {'held-out':>26} | English ord.")
for n, f in S.items():
    print(f"{n:52} {row(f, hc):>26} | {row(f, hindi_held):>26} | {sum(map(f, english))}/{len(english)}")
en_split_fp = sum(max(p.c['en_split'][k] for k in INJ) >= TI or p.c['en_split']['bulk_exfiltration'] >= TE for p in english)
print(f"\nsplit questions on raw English eval questions: {en_split_fp}/53 would be blocked")
f = all_multilingual(any2(translate, tsplit))
for p in hindi_all:
    if (p.attack and not f(p)) or (not p.attack and f(p)):
        print(("  MISSED " if p.attack else "  WRONG  "), p.split, p.text, "|", p.c["translate"][2][:70])
print("\nHinglish:", "translate OR translate+split ->", row(lambda p: stage1(p) or translate(p) or tsplit(p), hinglish))
