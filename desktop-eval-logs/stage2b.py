import json, os, sys; os.environ["HF_HUB_OFFLINE"]="1"; sys.path.insert(0,".")
from pathlib import Path
from laya import Router
sys.argv=["x"]; exec(open("../desktop-eval-logs/stage2.py").read().split("def chat(")[0])  # reuse SPLIT etc.
C = Path("/tmp/laya_probe/stage2_cache.json"); cache = json.loads(C.read_text())
r = Router(default="english", vega=False, max_loaded=2, device="cuda"); r.preload(["english"]); en = r.load("english")
for text, c in cache.items():
    english = c["translate"][2]
    a = en.predict({"user_message": english}, SPLIT)["answers"]
    c["translate_split"] = {k: a[k]["noul"] for k in SPLIT}
    a = en.predict({"user_message": text}, SPLIT)["answers"]
    c["en_split"] = {k: a[k]["noul"] for k in SPLIT}
C.write_text(json.dumps(cache, ensure_ascii=False)); print("ok", len(cache))
