"""Operations from the command line.

uv run python -m app.cli create-admin you@company.com "Your Name"   (asks for a password)
uv run python -m app.cli seed-demo          demo users, groups and grants (idempotent)
uv run python -m app.cli scan               sync the library folder into the database
uv run python -m app.cli index              run queued indexing jobs now
uv run python -m app.cli sync-authz         push all grants to OpenFGA
uv run python -m app.cli reindex --recreate rebuild every vector (after changing EMBEDDING_MODEL)
uv run python -m app.cli download-laya      fetch the Laya gate model once (needs internet)
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
from .models import Document, Folder, Grant, Group, GroupMember, User
from .services import build_services

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
# folder path -> [(subject_type, subject, relation)]
DEMO_GRANTS = {
    "general": [("group", "everyone", "viewer"), ("group", "hr", "editor")],
    "engineering": [("group", "engineering", "editor"), ("group", "leadership", "viewer")],
    "hr": [("group", "hr", "editor"), ("group", "leadership", "viewer")],
    "financials": [("group", "finance", "editor"), ("group", "leadership", "viewer")],
    # A one-off per-user grant: Carol may read the engineering budget, not the rest of finance.
    "financials/engineering-budget": [("user", "carol@acme.example.com", "viewer")],
    "management": [("group", "leadership", "editor")],
}


async def _services():
    return await build_services(get_settings(), session_factory())


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
        folders = {f.path: f for f in (await db.scalars(select(Folder))).all()}
        existing = {
            (g.folder_id, g.subject_type, g.subject_id, g.relation)
            for g in (await db.scalars(select(Grant))).all()
        }
        for path, grants in DEMO_GRANTS.items():
            if path not in folders:
                continue
            for subject_type, subject, relation in grants:
                subject_id = groups[subject].id if subject_type == "group" else users[subject].id
                key = (folders[path].id, subject_type, subject_id, relation)
                if key not in existing:
                    db.add(
                        Grant(
                            folder_id=key[0],
                            subject_type=subject_type,
                            subject_id=subject_id,
                            relation=relation,
                        )
                    )
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

    Router(default="english", vega=False, max_loaded=1, device="cpu").preload(["english"])
    print("Laya is cached; the gate runs offline from now on.")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="vault", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    admin = sub.add_parser("create-admin")
    admin.add_argument("email")
    admin.add_argument("name")
    for name in ("seed-demo", "scan", "index", "sync-authz", "download-laya"):
        sub.add_parser(name)
    rebuild = sub.add_parser("reindex")
    rebuild.add_argument("--recreate", action="store_true", help="drop and rebuild the vector collection")
    args = parser.parse_args()

    if args.command == "download-laya":
        download_laya()
        return
    commands = {
        "create-admin": lambda: create_admin(args.email, args.name),
        "seed-demo": seed_demo,
        "scan": scan,
        "index": index,
        "sync-authz": sync_authz,
        "reindex": lambda: reindex(args.recreate),
    }

    async def run() -> None:
        try:
            await commands[args.command]()
        finally:
            await dispose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
