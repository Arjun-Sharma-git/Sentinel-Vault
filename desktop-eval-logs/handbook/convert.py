"""Copy the GitLab handbook's Markdown into the vault library as gitlab-handbook/<section>/...

- _index.md -> index.md (the scanner hides names starting with "_")
- front-matter title -> "# title" when the page has no top-level heading
- Hugo shortcodes ({{< ... >}}, {{% ... %}}) removed
- root pages -> gitlab-handbook/home/, so the gitlab-handbook folder itself never needs a grant
"""
import re, shutil, sys
from pathlib import Path

src = Path(sys.argv[1]) / "content" / "handbook"
dst = Path(sys.argv[2])
commit = sys.argv[3]
FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
TITLE = re.compile(r"^(?:title|Title)\s*:\s*[\"']?(.+?)[\"']?\s*$", re.M)
SHORTCODE = re.compile(r"\{\{[<%].*?[%>]\}\}", re.S)

if dst.exists():
    shutil.rmtree(dst)
count = titled = 0
for path in sorted(src.rglob("*.md")):
    rel = path.relative_to(src)
    parts = list(rel.parts)
    if len(parts) == 1:
        parts = ["home", parts[0]]
    if parts[-1] == "_index.md":
        parts[-1] = "index.md"
    if any(p.startswith((".", "_")) for p in parts):
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    title = None
    m = FRONT.match(text)
    if m:
        t = TITLE.search(m.group(1))
        title = t.group(1).strip() if t else None
        body = text[m.end():]
    else:
        body = text
    body = SHORTCODE.sub("", body)
    if title and not re.search(r"^# \S", body, re.M):
        body = f"# {title}\n\n{body.lstrip()}"
        titled += 1
    out = dst.joinpath(*parts)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    count += 1

(dst / "home" / "ABOUT-THIS-COPY.md").write_text(f"""# About this copy of the GitLab Handbook

This folder is a copy of the [GitLab Handbook](https://handbook.gitlab.com/handbook/), (c) GitLab,
licensed under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Source:
https://gitlab.com/gitlab-com/content-sites/handbook at commit {commit}.

Changes made for this vault: only the Markdown pages were copied; section landing pages (_index.md)
were renamed index.md; front-matter titles were added as headings where a page had none; Hugo
shortcodes were removed; pages at the handbook root were moved into this home/ folder.
""", encoding="utf-8")
print(f"{count} pages copied ({titled} given a heading from their front matter) -> {dst}")
