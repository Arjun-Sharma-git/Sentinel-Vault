"""Knowledge sources: definitions, converters, install, grant planning and application."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.answer import SYSTEM_PROMPT
from app.cli import DEMO_USERS
from app.evaluation import load_questions
from app.retrieval import Passage, cap_per_document
from app.sources import (
    Source,
    SourceError,
    apply_grants,
    build,
    convert_copy,
    convert_hugo_markdown,
    in_source,
    install,
    load_source,
    load_sources,
    plan_grants,
)


def write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def source_file(directory: Path, **fields) -> Path:
    data = {"name": "kb", "title": "KB", "folder": "kb", "grants": []} | fields
    return write(directory / f"{data['name']}.yaml", yaml.safe_dump(data))


# ---- Definitions -----------------------------------------------------------------------------
def test_the_shipped_sources_are_valid():
    sources = load_sources()
    assert {"demo", "gitlab-handbook"} <= set(sources)
    handbook = sources["gitlab-handbook"]
    assert handbook.closed_root and handbook.require_all_granted and handbook.convert == "hugo-markdown"


@pytest.mark.parametrize(
    "fields, message",
    [
        ({"name": "Bad Name"}, "lowercase"),
        ({"folder": "a/b"}, "top-level"),
        ({"fetch": {}}, "exactly one of git or path"),
        ({"fetch": {"path": "x", "git": {"url": "u"}}}, "exactly one of git or path"),
        ({"grants": [{"group": "g", "user": "u@x", "folders": ["a"]}]}, "exactly one of group or user"),
        ({"grants": [{"group": "g", "folders": []}]}, "at least one folder"),
        ({"convert": "pandoc"}, "convert"),
        ({"surprise": 1}, "surprise"),
    ],
)
def test_invalid_definitions_are_rejected(tmp_path, fields, message):
    with pytest.raises(SourceError, match=message):
        load_source(source_file(tmp_path, **fields))


def test_the_file_name_must_match_the_source_name(tmp_path):
    path = source_file(tmp_path)
    path.rename(tmp_path / "other.yaml")
    with pytest.raises(SourceError, match="must match the file name"):
        load_source(tmp_path / "other.yaml")


def test_two_sources_cannot_claim_the_same_folder(tmp_path):
    source_file(tmp_path, name="one", folder="shared")
    source_file(tmp_path, name="two", folder="shared")
    with pytest.raises(SourceError, match="both claim"):
        load_sources(tmp_path)


# ---- Converters ------------------------------------------------------------------------------
def test_hugo_markdown_makes_pages_the_library_can_read(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    write(src / "content/_index.md", "---\ntitle: Home\n---\nWelcome")
    write(src / "content/people/_index.md", '---\ntitle: "People"\n---\nAbout {{< team >}} us')
    write(src / "content/people/leave.md", "---\ntitle: Leave\n---\n# Leave policy\n\n25 days")
    write(src / "content/_drafts/secret.md", "draft")
    write(src / "content/people/photo.png", "png")
    result = convert_hugo_markdown(src, dst, {"root": "content", "home": "home"})
    assert result.files == 3 and result.titled == 2 and result.skipped == ["_drafts/secret.md"]
    assert (dst / "home/index.md").read_text().startswith("# Home")  # root pages go to home/
    people = (dst / "people/index.md").read_text()
    assert people.startswith("# People") and "{{<" not in people  # title added, shortcode gone
    assert (dst / "people/leave.md").read_text().startswith("# Leave policy")  # own heading kept
    assert not (dst / "people/photo.png").exists()


def test_copy_keeps_files_and_skips_hidden_ones(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    write(src / "hr/policy.pdf")
    write(src / ".git/config")
    write(src / "hr/_scratch.md")
    result = convert_copy(src, dst, {})
    assert result.files == 1 and (dst / "hr/policy.pdf").exists()
    assert sorted(result.skipped) == [".git/config", "hr/_scratch.md"]


# ---- Install and build -----------------------------------------------------------------------
def test_a_source_only_replaces_its_own_folder(tmp_path):
    library = tmp_path / "library"
    write(library / "kb/old.md")
    write(library / "neighbour/keep.md")
    built = tmp_path / "built"
    write(built / "section/new.md")
    source = Source(name="kb", title="KB", folder="kb")
    assert install(source, built, library, {"kb": source}) == ["kb"]
    assert (library / "kb/section/new.md").exists() and not (library / "kb/old.md").exists()
    assert (library / "neighbour/keep.md").exists()


def test_a_root_source_cannot_overwrite_another_sources_folder(tmp_path):
    library = tmp_path / "library"
    write(library / "kb/page.md")
    built = tmp_path / "built"
    write(built / "kb/intruder.md")
    root = Source(name="demo", title="Demo", folder=".", closed_root=False, require_all_granted=False)
    other = Source(name="kb", title="KB", folder="kb")
    with pytest.raises(SourceError, match="belongs to another source"):
        install(root, built, library, {"demo": root, "kb": other})
    assert (library / "kb/page.md").exists()


def test_build_from_a_local_path_adds_an_attribution_page(tmp_path):
    upstream = tmp_path / "upstream"
    write(upstream / "guides/setup.md", "# Setup")
    library = tmp_path / "library"
    source = Source(
        name="kb", title="Team KB", folder="kb", license="CC0", fetch={"path": str(upstream)}, convert="copy"
    )
    result, replaced, revision = build(source, tmp_path / "cache", library, {"kb": source})
    assert (result.files, replaced, revision) == (1, ["kb"], "local")
    about = (library / "kb/home/ABOUT-THIS-COPY.md").read_text()
    assert "Team KB" in about and "CC0" in about
    assert not any(p.name.startswith(".sync-") for p in tmp_path.iterdir())  # staging cleaned up


# ---- Grant planning --------------------------------------------------------------------------
FOLDERS = {"kb", "kb/home", "kb/people", "kb/people/leave", "kb/legal", "general"}


def kb(grants, **fields) -> Source:
    return Source(name="kb", title="KB", folder="kb", grants=grants, **fields)


def test_a_complete_plan_has_no_problems():
    plan = plan_grants(
        kb(
            [
                {"group": "everyone", "folders": ["home", "people"]},
                {"group": "leadership", "folders": ["legal"]},
            ]
        ),
        FOLDERS,
    )
    assert plan.problems == []
    assert ("kb/people", "group", "everyone", "viewer") in plan.wanted


def test_granting_the_source_root_is_refused():
    plan = plan_grants(kb([{"group": "everyone", "folders": ["."]}]), FOLDERS)
    assert plan.root_granted and "closed_root" in plan.problems[0]


def test_sections_without_a_grant_are_reported():
    plan = plan_grants(kb([{"group": "everyone", "folders": ["home"]}]), FOLDERS)
    assert plan.unmapped == ["kb/legal", "kb/people"]
    relaxed = plan_grants(
        kb([{"group": "everyone", "folders": ["home"]}], require_all_granted=False), FOLDERS
    )
    assert relaxed.unmapped == []


def test_grants_on_missing_folders_are_reported():
    plan = plan_grants(kb([{"group": "everyone", "folders": ["home", "people", "legal", "gone"]}]), FOLDERS)
    assert plan.unknown_folders == ["kb/gone"]


# ---- Grants in the database ------------------------------------------------------------------
def test_demo_grants_come_from_the_demo_source_and_apply_idempotently(api):
    """seed-demo applied sources/demo.yaml; applying it again adds nothing."""
    services = api.client.app.state.services
    demo = load_sources()["demo"]

    async def run():
        async with services.sessions() as db:
            return await apply_grants(db, demo, dry_run=True)

    result = api.client.portal.call(run)
    assert result == {"added": 0, "existing": 10, "problems": []}


def test_a_plan_with_problems_writes_nothing(api):
    services = api.client.app.state.services
    broken = Source(
        name="broken",
        title="Broken",
        folder=".",
        closed_root=False,
        require_all_granted=False,
        grants=[{"group": "no-such-group", "folders": ["general"]}],
    )

    async def run():
        async with services.sessions() as db:
            return await apply_grants(db, broken)

    result = api.client.portal.call(run)
    assert result["added"] == 0 and "unknown subjects: group no-such-group" in result["problems"][0]


# ---- Retrieval and answers -------------------------------------------------------------------
def passage(doc: str, n: int) -> Passage:
    return Passage(f"{doc}-{n}", doc, doc, "f", "f", f"{doc}.md", "", "text", 0.0)


def test_one_document_cannot_fill_every_slot():
    ranked = [passage("guide", i) for i in range(6)] + [passage("policy", 0), passage("faq", 0)]
    chosen = cap_per_document(ranked, cap=2, limit=4)
    assert [p.chunk_id for p in chosen] == ["guide-0", "guide-1", "policy-0", "faq-0"]


def test_the_cap_never_returns_fewer_passages_than_exist():
    ranked = [passage("only", i) for i in range(5)]
    assert len(cap_per_document(ranked, cap=2, limit=4)) == 4
    assert [p.chunk_id for p in cap_per_document(ranked, cap=0, limit=3)] == ["only-0", "only-1", "only-2"]


def test_answers_follow_the_language_of_the_question():
    assert "language the question is written in" in SYSTEM_PROMPT


# ---- Eval sets -------------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["demo", "gitlab-handbook"])
def test_each_source_eval_set_is_well_formed(name):
    source = load_sources()[name]
    questions = load_questions(source.eval_path())
    personas = {email.split("@")[0] for email, *_ in DEMO_USERS}
    assert questions and {q.persona for q in questions} <= personas
    for q in questions:
        if q.answerable:
            assert q.expect and q.facts, q.ask
            if source.folder != ".":
                assert all(e.startswith(source.folder + "/") for e in q.expect), q.ask
        else:
            assert not q.expect, q.ask


# ---- Scoping a question to sources -----------------------------------------------------------
def test_folders_belong_to_their_source():
    sources = load_sources()
    demo, handbook = sources["demo"], sources["gitlab-handbook"]
    assert in_source("gitlab-handbook/finance", handbook, sources)
    assert not in_source("general", handbook, sources)
    assert in_source("financials/engineering-budget", demo, sources)  # root source: its own folders
    assert not in_source("gitlab-handbook/finance", demo, sources)  # but not another source's


def test_a_question_scoped_to_a_source_only_searches_it(api):
    question = "How many days of paid time off do full-time employees get?"
    scoped = api.ask("alice", question, sources=["demo"])
    assert scoped["answer"]["citations"]
    assert all(not c["path"].startswith("gitlab-handbook/") for c in scoped["answer"]["citations"])
    # The test library has no handbook pages, so scoping to it leaves nothing to answer from.
    empty = api.ask("alice", question, sources=["gitlab-handbook"])
    assert empty["answer"]["citations"] == []


def test_scoping_never_widens_access(api):
    """Dave can't read hr/; naming the demo source doesn't change that."""
    result = api.ask("dave", "What is the pay range for an L5 engineer?", sources=["demo"])
    assert not any(c["path"].startswith("hr/") for c in result["answer"]["citations"])


def test_unknown_sources_are_rejected(api):
    response = api.post("alice", "/api/ask", json={"question": "hi", "sources": ["nope"]})
    assert response.status_code == 422 and "unknown sources: nope" in response.text
