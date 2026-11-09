"""Operations from the command line.

uv run python -m app.cli create-admin you@company.com "Your Name"   (asks for a password)
uv run python -m app.cli seed-demo          demo users, groups and grants (idempotent)
uv run python -m app.cli scan               sync the library folder into the database
uv run python -m app.cli index              run queued indexing jobs now
uv run python -m app.cli sync-authz         push all grants to OpenFGA
uv run python -m app.cli reindex --recreate rebuild every vector (after changing EMBEDDING_MODEL)
uv run python -m app.cli download-laya      fetch the Laya gate model once (needs internet)
uv run python -m app.cli download-docling   fetch Docling's PDF models once (the parser runs offline)
uv run python -m app.cli download-reranker  fetch the configured reranker model once
uv run python -m app.cli eval [--answers]   score retrieval (and answers) on eval/golden.yaml
uv run python -m app.cli gate-eval          fit and test the Laya gate thresholds on eval/gate_hindi.yaml
uv run python -m app.cli gate-export        add audit-log prompts to eval/gate_labels.local.yaml for labelling
uv run python -m app.cli source list        knowledge sources (backend/sources/*.yaml)
uv run python -m app.cli source sync NAME   fetch and convert a source into the library, then scan it
uv run python -m app.cli source grant NAME  apply a source's folder grants (idempotent; --dry-run)
uv run python -m app.cli source eval NAME   run a source's eval questions [--rerankers ..] [--answers]
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from sqlalchemy import select, update

from .auth import MIN_PASSWORD_LENGTH, hash_password
from .config import get_settings
from .db import dispose, session_factory
from .ingest.library import enqueue_index, scan_library
from .ingest.worker import drain
from .models import Document, Group, GroupMember, User
from .services import build_services
from .sources import apply_grants, get_source, load_sources, source_folder_ids

DEMO_PASSWORD = os.environ.get("VAULT_DEMO_PASSWORD", "vault-demo-2026")

DEMO_GROUPS = {
    "everyone": "Every employee",
    "engineering": "Engineering team",
    "hr": "People team",
    "finance": "Finance team",
    "leadership": "Executive team",
}
DEMO_USERS = [  # email, name, admin, groups
    ("alice@acme.example.com", "Alice Admin", True, ["everyone", "leadership"]),
    ("bob@acme.example.com", "Bob Finance", False, ["everyone", "finance"]),
    ("erin@acme.example.com", "Erin People", False, ["everyone", "hr"]),
    ("carol@acme.example.com", "Carol Engineer", False, ["everyone", "engineering"]),
    ("dave@acme.example.com", "Dave Intern", False, ["everyone"]),
]
# Demo grants live in sources/demo.yaml (applied by seed-demo).


async def _services():
    """The app's services without the prompt gate: no CLI command that builds them asks
    questions through the gate (gate-eval loads its own), and Laya would put a second copy of
    its models on the GPU next to a running API's."""
    settings = get_settings().model_copy(update={"gate_engine": "off", "gate_second_stage": "off"})
    return await build_services(settings, session_factory())


async def create_admin(email: str, name: str) -> None:
    password = getpass.getpass(f"Password for {email} (min {MIN_PASSWORD_LENGTH} chars): ")
    if len(password) < MIN_PASSWORD_LENGTH:
        sys.exit("Password too short.")
    async with session_factory()() as db:
        existing = await db.scalar(select(User).where(User.email == email.lower()))
        if existing:
            existing.is_admin, existing.password_hash = True, hash_password(password)
        else:
            db.add(User(email=email.lower(), name=name, password_hash=hash_password(password), is_admin=True))
        await db.commit()
    print(f"Admin ready: {email}")


async def seed_demo() -> None:
    services = await _services()
    await seed_demo_into(services)
    print(f"Demo users (password '{DEMO_PASSWORD}'):", ", ".join(e for e, *_ in DEMO_USERS))
    await services.close()


async def seed_demo_into(services) -> None:
    """Create the demo groups, users and grants (idempotent), then sync the authz backend."""
    async with services.sessions() as db:
        await scan_library(db, services.vectors, services.settings.library_dir)
        groups = {g.name: g for g in (await db.scalars(select(Group))).all()}
        for name, description in DEMO_GROUPS.items():
            if name not in groups:
                groups[name] = Group(name=name, description=description)
                db.add(groups[name])
        await db.flush()
        users = {u.email: u for u in (await db.scalars(select(User))).all()}
        for email, name, admin, group_names in DEMO_USERS:
            user = users.get(email)
            if user is None:
                user = User(
                    email=email, name=name, is_admin=admin, password_hash=hash_password(DEMO_PASSWORD)
                )
                db.add(user)
                await db.flush()
                users[email] = user
                db.add_all(GroupMember(group_id=groups[g].id, user_id=user.id) for g in group_names)
        await db.flush()
        result = await apply_grants(db, get_source("demo"), skip_missing=True)
        if result["problems"]:
            raise SystemExit(f"demo grants: {'; '.join(result['problems'])}")
        await db.commit()
    await services.authz.sync()


async def scan() -> None:
    services = await _services()
    async with services.sessions() as db:
        print(await scan_library(db, services.vectors, services.settings.library_dir))
    await services.authz.sync()
    await services.close()


async def index() -> None:
    services = await _services()
    print("jobs run:", await drain(services.sessions, services.indexer))
    await services.close()


async def sync_authz() -> None:
    services = await _services()
    print(await services.authz.sync())
    await services.close()


async def reindex(recreate: bool) -> None:
    services = await _services()
    if recreate:
        await services.vectors.recreate(await services.embedder.probe())
    async with services.sessions() as db:
        ids = (await db.scalars(select(Document.id))).all()
        await db.execute(update(Document).values(status="pending"))
        for document_id in ids:
            await enqueue_index(db, document_id)
        await db.commit()
    print(f"queued {len(ids)} documents; jobs run:", await drain(services.sessions, services.indexer))
    await services.close()


def download_laya() -> None:
    os.environ["HF_HUB_OFFLINE"] = "0"
    from laya import Router

    from .gate import LayaGate

    Router(default="english", vega=False, max_loaded=2, device="cpu").preload(LayaGate.CHECKPOINTS)
    print("Laya (english + multilingual) is cached; the gate runs offline from now on.")


def download_docling() -> None:
    """Fetch Docling's PDF models (layout, tables, OCR) into the cache by parsing a blank page.

    The parser normally runs with no network (ingest/sandbox.py), so it can only use models
    that are already cached.
    """
    import tempfile
    from pathlib import Path

    import pypdfium2

    from .ingest.parse import _parse_rich

    os.environ["HF_HUB_OFFLINE"] = "0"
    with tempfile.TemporaryDirectory() as tmp:
        blank = Path(tmp) / "blank.pdf"
        pdf = pypdfium2.PdfDocument.new()
        pdf.new_page(612, 792)
        pdf.save(blank)
        _parse_rich(blank)
    print("Docling's models are cached; PDF parsing works offline from now on.")


async def run_eval(rerankers: list[str], answers: bool) -> None:
    from .config import BASE_DIR
    from .evaluation import evaluate, render, save

    services = await _services()
    try:
        report = await evaluate(services, rerankers, answers)
    finally:
        await services.close()
    print(render(report))
    print(f"\nfull report: {save(report, BASE_DIR / 'eval' / 'results')}")


def download_reranker() -> None:
    from .config import get_settings
    from .rerank import make_reranker

    os.environ["HF_HUB_OFFLINE"] = "0"
    settings = get_settings()
    reranker = make_reranker(settings.reranker, settings.reranker_model)
    if not reranker.enabled:
        print("RERANKER=off; nothing to download.")
        return
    reranker.load()  # type: ignore[attr-defined]
    print(f"{reranker.name} is cached.")


async def source_list() -> None:
    from .models import Document

    sources = load_sources()
    async with session_factory()() as db:
        paths = [
            p
            for (p,) in (
                await db.execute(select(Document.rel_path).where(Document.status == "indexed"))
            ).all()
        ]
    owned = {s.folder for s in sources.values() if s.folder != "."}
    for source in sources.values():
        if source.folder == ".":
            count = sum(1 for p in paths if p.split("/", 1)[0] not in owned)
        else:
            count = sum(1 for p in paths if p.startswith(source.folder + "/"))
        how = "git" if source.fetch and source.fetch.git else "path" if source.fetch else "by hand"
        print(
            f"{source.name:20} folder {source.folder:18} {count:6} indexed documents  "
            f"fetch: {how}  {source.title}"
        )


async def source_sync(name: str, scan_after: bool = True) -> None:
    from .sources import build

    settings = get_settings()
    sources = load_sources()
    source = sources[name] if name in sources else get_source(name)
    cache = settings.sources_cache_dir or settings.library_dir.parent / "sources"
    result, replaced, revision = build(source, cache, settings.library_dir, sources)
    print(
        f"{source.name} at {revision}: {result.files} files ({result.titled} titled from front matter, "
        f"{len(result.skipped)} hidden paths skipped) -> {', '.join(replaced)}"
    )
    if scan_after:
        services = await _services()
        try:
            async with services.sessions() as db:
                print(await scan_library(db, services.vectors, settings.library_dir))
        finally:
            await services.close()
        print("queued for indexing; the API's worker (or `app.cli index`) picks them up.")


async def source_grant(name: str, dry_run: bool) -> None:
    source = get_source(name)
    services = await _services()
    try:
        async with services.sessions() as db:
            result = await apply_grants(db, source, dry_run=dry_run)
        if result["problems"]:
            raise SystemExit(f"{name}: nothing written. " + "; ".join(result["problems"]))
        print(
            f"{name}: {result['added']} grants {'to add' if dry_run else 'added'}, "
            f"{result['existing']} already there"
        )
        if not dry_run and result["added"]:
            print("authz sync:", await services.authz.sync())
    finally:
        await services.close()


async def source_eval(name: str, rerankers: list[str], answers: bool, mixed: bool = False) -> None:
    from .config import BASE_DIR
    from .evaluation import evaluate, load_questions, render, save

    source = get_source(name)
    path = source.eval_path()
    if path is None or not path.exists():
        raise SystemExit(f"{name}: no eval questions ({source.eval or 'eval not set'})")
    services = await _services()
    try:
        within = None
        if not mixed:  # search the source's own folders, as a user who picked it would
            async with services.sessions() as db:
                within = await source_folder_ids(db, [name], load_sources())
        report = await evaluate(services, rerankers, answers, load_questions(path), within)
    finally:
        await services.close()
    report["source"], report["scope"] = name, "everything readable" if mixed else f"source {name}"
    print(render(report))
    print(f"\nfull report: {save(report, BASE_DIR / 'eval' / 'results')}")


async def gate_export(days: int, sample: int) -> None:
    from .gate_labels import LABELS, export_for_labelling

    async with session_factory()() as db:
        counts = await export_for_labelling(db, LABELS, days, sample)
    print(
        f"added {counts['review']} review, {counts['blocked']} blocked and {counts['sample']} sampled "
        f"prompts; "
        f"{counts['labelled']} of {counts['total']} labelled in {LABELS}"
    )


def gate_eval(budget: float, second_stage: str | None, similarity: float | None, no_similarity: bool) -> None:
    """Score eval/gate_hindi.yaml and the golden questions with Laya; fit per-checkpoint thresholds."""
    from .config import BASE_DIR, get_settings
    from .devices import resolve_device
    from .embeddings import OllamaEmbedder
    from .gate import LayaGate, SecondStage, Thresholds
    from .gate_eval import evaluate_gate, render_gate, save_gate

    settings = get_settings()
    engine = LayaGate(device=resolve_device(settings.torch_device).torch_device)  # no heuristic fallback
    default = Thresholds(settings.gate_injection_threshold, settings.gate_exfiltration_threshold)
    scope = second_stage or settings.gate_second_stage
    second = (
        None
        if scope == "off"
        else SecondStage(
            settings.ollama_url,
            settings.gate_second_stage_model or settings.llm_model,
            engine,
            default,
            settings.gate_second_stage_timeout_seconds,
        )
    )
    threshold = (
        None
        if no_similarity
        else (similarity if similarity is not None else settings.gate_similarity_threshold)
    )
    embedder = (
        OllamaEmbedder(settings.ollama_url, settings.embedding_model) if threshold is not None else None
    )
    report = evaluate_gate(
        engine, budget, default, second, scope, settings.gate_review_threshold, embedder, threshold
    )
    print(render_gate(report))
    print(f"\nfull report: {save_gate(report, BASE_DIR / 'eval' / 'results')}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="vault", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    admin = sub.add_parser("create-admin")
    admin.add_argument("email")
    admin.add_argument("name")
    for name in (
        "seed-demo",
        "scan",
        "index",
        "sync-authz",
        "download-laya",
        "download-docling",
        "download-reranker",
    ):
        sub.add_parser(name)
    evaluation = sub.add_parser("eval", help="score retrieval (and answers) on eval/golden.yaml")
    evaluation.add_argument(
        "--rerankers", default="off,cross-encoder", help="comma-separated: off, cross-encoder, qwen"
    )
    evaluation.add_argument("--answers", action="store_true", help="also generate and score answers (slow)")
    gates = sub.add_parser("gate-eval", help="fit and test the gate thresholds on eval/gate_hindi.yaml")
    gates.add_argument(
        "--budget", type=float, default=0.02, help="largest share of ordinary prompts the gate may block"
    )
    gates.add_argument("--second-stage", choices=["off", "review", "all"], help="default: GATE_SECOND_STAGE")
    gates.add_argument(
        "--similarity", type=float, help="margin threshold (default: GATE_SIMILARITY_THRESHOLD)"
    )
    gates.add_argument("--no-similarity", action="store_true", help="leave the similarity check out")
    export = sub.add_parser("gate-export", help="add audit-log prompts to eval/gate_labels.local.yaml")
    export.add_argument("--days", type=int, default=30, help="how far back to look")
    export.add_argument("--sample", type=int, default=50, help="random ordinary questions to add")
    src = sub.add_parser("source", help="knowledge sources: list, sync, grant, eval")
    src_sub = src.add_subparsers(dest="action", required=True)
    src_sub.add_parser("list")
    sync_p = src_sub.add_parser("sync")
    sync_p.add_argument("name")
    sync_p.add_argument("--no-scan", action="store_true", help="convert and install only")
    grant_p = src_sub.add_parser("grant")
    grant_p.add_argument("name")
    grant_p.add_argument("--dry-run", action="store_true")
    eval_p = src_sub.add_parser("eval")
    eval_p.add_argument("name")
    eval_p.add_argument(
        "--rerankers", default="off,cross-encoder", help="comma-separated: off, cross-encoder, qwen"
    )
    eval_p.add_argument("--answers", action="store_true")
    eval_p.add_argument("--mixed", action="store_true", help="search the whole library, not just this source")
    rebuild = sub.add_parser("reindex")
    rebuild.add_argument("--recreate", action="store_true", help="drop and rebuild the vector collection")
    args = parser.parse_args()

    downloads = {
        "download-laya": download_laya,
        "download-docling": download_docling,
        "download-reranker": download_reranker,
    }
    if args.command in downloads:
        downloads[args.command]()
        return
    if args.command == "gate-eval":
        gate_eval(args.budget, args.second_stage, args.similarity, args.no_similarity)
        return
    commands = {
        "create-admin": lambda: create_admin(args.email, args.name),
        "seed-demo": seed_demo,
        "scan": scan,
        "index": index,
        "sync-authz": sync_authz,
        "reindex": lambda: reindex(args.recreate),
        "gate-export": lambda: gate_export(args.days, args.sample),
        "source": lambda: {
            "list": lambda: source_list(),
            "sync": lambda: source_sync(args.name, not args.no_scan),
            "grant": lambda: source_grant(args.name, args.dry_run),
            "eval": lambda: source_eval(
                args.name,
                [k.strip() for k in args.rerankers.split(",") if k.strip()],
                args.answers,
                args.mixed,
            ),
        }[args.action](),
        "eval": lambda: run_eval([k.strip() for k in args.rerankers.split(",") if k.strip()], args.answers),
    }

    async def run() -> None:
        try:
            await commands[args.command]()
        finally:
            await dispose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
