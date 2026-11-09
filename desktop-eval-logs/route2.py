import os, sys, yaml; sys.path.insert(0, ".")
from laya import lang
d = yaml.safe_load(open("eval/gate_hindi.yaml"))
from app.evaluation import load_questions
hl = d["hinglish"]["benign"] + [a[1] for a in d["hinglish"]["attacks"]]
en = [q.ask for q in load_questions()]
for name, rows in (("hinglish", hl), ("english", en)):
    res = [lang.guess_latin_language(t) for t in rows]
    import collections; print(name, collections.Counter(map(str, res)))
    print(name, "is_english:", sum(bool(lang.is_english(t)) for t in rows), "/", len(rows))
