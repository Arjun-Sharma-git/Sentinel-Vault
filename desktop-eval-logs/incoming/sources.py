"""Knowledge sources: each knowledge base in the library is described by one YAML file.

    backend/sources/<name>.yaml

A source says where its documents come from (`fetch`), how they become library files
(`convert`), which top-level library folder they live in (`folder`), who may read which part
(`grants`) and which eval questions measure it (`eval`). The CLI works the same way for every
source:

    uv run python -m app.cli source list
    uv run python -m app.cli source sync <name>     fetch, convert, swap into the library, scan
    uv run python -m app.cli source grant <name>    apply the grants (idempotent), sync OpenFGA
    uv run python -m app.cli source eval <name>     run the source's eval questions

Safety rules, checked before anything is written:
- a source only ever replaces its own folder (or, for folder ".", the top-level folders it
  produces); two sources may not claim the same folder;
- with `closed_root` (the default) the source's own folder gets no grant, because a grant there
  would pass down to every subfolder, restricted ones included;
- with `require_all_granted` (the default) every top-level subfolder must be covered by a grant,
  so a new section that appears after a sync is reported instead of silently staying closed;
- grants are only ever added; removing access stays a deliberate admin action.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import BASE_DIR

SOURCES_DIR = BASE_DIR / "sources"
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class SourceError(ValueError):
    pass


class GitFetch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    ref: str = "main"
    sparse: list[str] = Field(default_factory=list)  # no-cone patterns, e.g. "/content/**/*.md"


class Fetch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    git: GitFetch | None = None
    path: str | None = None  # a local directory; relative paths are relative to the backend

    @model_validator(mode="after")
    def exactly_one(self) -> Fetch:
        if (self.git is None) == (self.path is None):
            raise ValueError("fetch needs exactly one of git or path")
        return self


class GrantRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group: str | None = None
    user: str | None = None  # an email address
    relation: Literal["viewer", "editor"] = "viewer"
    folders: list[str]  # relative to the source's folder; "." is the source folder itself

    @model_validator(mode="after")
    def one_subject(self) -> GrantRule:
        if (self.group is None) == (self.user is None):
            raise ValueError("a grant needs exactly one of group or user")
        if not self.folders:
            raise ValueError("a grant needs at least one folder")
        return self


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    title: str
    folder: str  # top-level library folder, or "." for folders at the library root
    license: str | None = None
    homepage: str | None = None
    fetch: Fetch | None = None  # None: the files are maintained in the library by hand
    convert: Literal["copy", "hugo-markdown"] = "copy"
    convert_options: dict[str, str] = Field(default_factory=dict)
    closed_root: bool = True
    require_all_granted: bool = True
    grants: list[GrantRule] = Field(default_factory=list)
    eval: str | None = None  # question file, relative to the backend directory

    @field_validator("name")
    @classmethod
    def slug(cls, value: str) -> str:
        if not NAME.match(value):
            raise ValueError("name must be lowercase letters, digits and hyphens")
        return value

    @field_validator("folder")
    @classmethod
    def top_level(cls, value: str) -> str:
        if value != "." and (not NAME.match(value)):
            raise ValueError('folder must be "." or one top-level folder name (lowercase, hyphens)')
        return value

    def library_path(self, library: Path, relative: str = ".") -> str:
        """Library-relative folder path for a folder inside this source."""
        parts = [p for p in (self.folder, relative) if p not in (".", "")]
        return "/".join(parts)

    def eval_path(self) -> Path | None:
        return (BASE_DIR / self.eval).resolve() if self.eval else None


def load_source(path: Path) -> Source:
    try:
        raw = yaml.safe_load(path.read_text()) or {}
        source = Source.model_validate(raw)
    except Exception as exc:
        raise SourceError(f"{path.name}: {exc}") from exc
    if source.name != path.stem:
        raise SourceError(f"{path.name}: name {source.name!r} must match the file name")
    return source


def load_sources(directory: Path = SOURCES_DIR) -> dict[str, Source]:
    sources = {p.stem: load_source(p) for p in sorted(directory.glob("*.yaml"))}
    owners: dict[str, str] = {}
    for source in sources.values():
        if source.folder != "." and source.folder in owners:
            raise SourceError(f"sources {owners[source.folder]!r} and {source.name!r} both claim {source.folder!r}")
        owners[source.folder] = source.name
    return sources


def get_source(name: str, directory: Path = SOURCES_DIR) -> Source:
    sources = load_sources(directory)
    if name not in sources:
        raise SourceError(f"no source {name!r}; known: {', '.join(sources) or 'none'}")
    return sources[name]


# ---- Fetch -----------------------------------------------------------------------------------
def fetch(source: Source, cache: Path) -> tuple[Path, str]:
    """Bring the source's upstream files to disk. Returns (directory, revision)."""
    if source.fetch is None:
        raise SourceError(f"source {source.name!r} has no fetch section; its files are kept in the library by hand")
    if source.fetch.path is not None:
        path = Path(source.fetch.path)
        path = path if path.is_absolute() else (BASE_DIR / path).resolve()
        if not path.is_dir():
            raise SourceError(f"{source.name}: fetch path {path} is not a directory")
        return path, "local"
    git = source.fetch.git
    assert git is not None
    target = cache / source.name
    if not (target / ".git").exists():
        cache.mkdir(parents=True, exist_ok=True)
        args = ["git", "clone", "--depth", "1", "--branch", git.ref]
        if git.sparse:
            args += ["--filter=blob:none", "--sparse"]
        _run([*args, git.url, str(target)])
        if git.sparse:
            _run(["git", "-C", str(target), "sparse-checkout", "set", "--no-cone", *git.sparse])
    else:
        _run(["git", "-C", str(target), "fetch", "--depth", "1", "origin", git.ref])
        _run(["git", "-C", str(target), "reset", "--hard", "FETCH_HEAD"])
    revision = _run(["git", "-C", str(target), "log", "-1", "--format=%h"]).strip()
    return target, revision


def _run(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise SourceError(f"{' '.join(args[:4])} failed: {result.stderr.strip()[-500:]}")
    return result.stdout


# ---- Convert ---------------------------------------------------------------------------------
FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
TITLE = re.compile(r"^(?:title|Title)\s*:\s*[\"']?(.+?)[\"']?\s*$", re.M)
SHORTCODE = re.compile(r"\{\{[<%].*?[%>]\}\}", re.S)


@dataclass
class ConvertResult:
    files: int = 0
    titled: int = 0
    skipped: list[str] = field(default_factory=list)


def _hidden(parts: tuple[str, ...] | list[str]) -> bool:
    return any(p.startswith((".", "_")) for p in parts)


def convert_copy(src: Path, dst: Path, options: dict[str, str]) -> ConvertResult:
    """Copy files as they are (the library scanner decides which types it can read)."""
    root = src / options.get("root", ".")
    result = ConvertResult()
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if path.is_symlink() or not path.is_file():
            continue
        if _hidden(rel.parts):
            result.skipped.append(str(rel))
            continue
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, out)
        result.files += 1
    return result


def convert_hugo_markdown(src: Path, dst: Path, options: dict[str, str]) -> ConvertResult:
    """Hugo content trees (GitLab handbook and similar): Markdown only.

    _index.md -> index.md (the scanner hides names starting with "_"); a front-matter title
    becomes a "# heading" when the page has none; Hugo shortcodes are removed; pages at the root
    go into `home` so the source folder itself never needs a grant."""
    root = src / options.get("root", ".")
    home = options.get("home", "home")
    result = ConvertResult()
    for path in sorted(root.rglob("*.md")):
        parts = list(path.relative_to(root).parts)
        if len(parts) == 1:
            parts = [home, parts[0]]
        if parts[-1] == "_index.md":
            parts[-1] = "index.md"
        if _hidden(parts):
            result.skipped.append("/".join(parts))
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        title = None
        match = FRONT.match(text)
        if match:
            found = TITLE.search(match.group(1))
            title = found.group(1).strip() if found else None
            body = text[match.end() :]
        else:
            body = text
        body = SHORTCODE.sub("", body)
        if title and not re.search(r"^# \S", body, re.M):
            body = f"# {title}\n\n{body.lstrip()}"
            result.titled += 1
        out = dst.joinpath(*parts)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body, encoding="utf-8")
        result.files += 1
    return result


CONVERTERS = {"copy": convert_copy, "hugo-markdown": convert_hugo_markdown}


def attribution(source: Source, revision: str) -> str:
    lines = [f"# About this copy of {source.title}", ""]
    if source.homepage:
        lines.append(f"Source: {source.homepage}")
    if source.license:
        lines.append(f"License: {source.license}")
    if source.fetch and source.fetch.git:
        lines.append(f"Repository: {source.fetch.git.url} at {revision}")
    lines += ["", f"Converted with `{source.convert}` by `app.cli source sync {source.name}`."]
    return "\n".join(lines) + "\n"


def install(source: Source, built: Path, library: Path, all_sources: dict[str, Source]) -> list[str]:
    """Swap the built tree into the library. Replaces only what this source owns. Returns the
    library-relative folders it replaced."""
    library.mkdir(parents=True, exist_ok=True)
    if source.folder != ".":
        targets = [(built, library / source.folder)]
    else:
        others = {s.folder for s in all_sources.values() if s.name != source.name and s.folder != "."}
        targets = []
        for child in sorted(p for p in built.iterdir() if p.is_dir()):
            if child.name in others:
                raise SourceError(f"{source.name}: produces {child.name!r}, which belongs to another source")
            targets.append((child, library / child.name))
        loose = [p.name for p in built.iterdir() if p.is_file()]
        if loose:
            raise SourceError(f'{source.name}: folder "." sources may only produce folders, found {loose[:3]}')
    replaced = []
    for new, target in targets:
        old = target.with_name(target.name + ".old-sync")
        shutil.rmtree(old, ignore_errors=True)
        if target.exists():
            target.rename(old)
        shutil.move(str(new), target)
        shutil.rmtree(old, ignore_errors=True)
        replaced.append(str(target.relative_to(library)))
    return replaced


def build(source: Source, cache: Path, library: Path, all_sources: dict[str, Source]) -> tuple[ConvertResult, list[str], str]:
    upstream, revision = fetch(source, cache)
    staging = library.parent / f".sync-{source.name}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        result = CONVERTERS[source.convert](upstream, staging, source.convert_options)
        if source.folder != ".":
            about = staging / source.convert_options.get("home", "home") / "ABOUT-THIS-COPY.md"
            about.parent.mkdir(parents=True, exist_ok=True)
            about.write_text(attribution(source, revision), encoding="utf-8")
        replaced = install(source, staging, library, all_sources)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return result, replaced, revision


# ---- Grants ----------------------------------------------------------------------------------
@dataclass
class GrantPlan:
    """What applying a source's grants would do, computed without writing anything."""

    wanted: list[tuple[str, str, str, str]] = field(default_factory=list)  # (folder, type, subject, relation)
    unmapped: list[str] = field(default_factory=list)  # top-level subfolders no grant covers
    unknown_folders: list[str] = field(default_factory=list)
    root_granted: bool = False

    @property
    def problems(self) -> list[str]:
        out = []
        if self.root_granted:
            out.append("a grant targets the source folder itself, which closed_root forbids")
        if self.unknown_folders:
            out.append(f"grants name folders that don't exist: {', '.join(self.unknown_folders)}")
        if self.unmapped:
            out.append(f"no grant covers these sections: {', '.join(self.unmapped)}")
        return out


def plan_grants(source: Source, folder_paths: set[str]) -> GrantPlan:
    plan = GrantPlan()
    base = source.library_path(Path("."))
    for rule in source.grants:
        subject_type, subject = ("group", rule.group) if rule.group else ("user", rule.user)
        for rel in rule.folders:
            path = source.library_path(Path("."), rel)
            if source.closed_root and source.folder != "." and rel in (".", ""):
                plan.root_granted = True
                continue
            if path not in folder_paths:
                plan.unknown_folders.append(path)
                continue
            plan.wanted.append((path, subject_type, subject or "", rule.relation))
    if source.require_all_granted:
        if source.folder == ".":
            raise SourceError(f'{source.name}: require_all_granted needs a source folder, not "."')
        sections = {p for p in folder_paths if p.startswith(base + "/") and p.count("/") == 1}
        covered = {f for f, *_ in plan.wanted}
        plan.unmapped = sorted(s for s in sections if not any(s == c or s.startswith(c + "/") for c in covered))
    plan.unknown_folders = sorted(set(plan.unknown_folders))
    return plan


async def apply_grants(
    db,  # noqa: ANN001
    source: Source,
    dry_run: bool = False,
    skip_missing: bool = False,
) -> dict[str, object]:
    """Add the source's grants that don't exist yet. Refuses (writes nothing) if the plan has
    problems. `skip_missing` drops grants for folders that aren't in the library instead of
    refusing (seed-demo on a library without some demo folders)."""
    from sqlalchemy import select

    from .models import Folder, Grant, Group, User

    folders = {f.path: f for f in (await db.scalars(select(Folder))).all()}
    plan = plan_grants(source, set(folders))
    if skip_missing:
        plan.unknown_folders = []
    groups = {g.name: g.id for g in (await db.scalars(select(Group))).all()}
    users = {u.email: u.id for u in (await db.scalars(select(User))).all()}
    missing = sorted(
        {f"group {s}" for _, t, s, _ in plan.wanted if t == "group" and s not in groups}
        | {f"user {s}" for _, t, s, _ in plan.wanted if t == "user" and s.lower() not in users}
    )
    problems = plan.problems + ([f"unknown subjects: {', '.join(missing)}"] if missing else [])
    if problems:
        return {"added": 0, "existing": 0, "problems": problems}
    have = {(g.folder_id, g.subject_type, g.subject_id, g.relation) for g in (await db.scalars(select(Grant))).all()}
    added = existing = 0
    for path, subject_type, subject, relation in plan.wanted:
        subject_id = groups[subject] if subject_type == "group" else users[subject.lower()]
        key = (folders[path].id, subject_type, subject_id, relation)
        if key in have:
            existing += 1
            continue
        have.add(key)
        added += 1
        if not dry_run:
            db.add(Grant(folder_id=key[0], subject_type=subject_type, subject_id=subject_id, relation=relation))
    if not dry_run:
        await db.commit()
    return {"added": added, "existing": existing, "problems": []}
