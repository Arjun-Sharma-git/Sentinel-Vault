"""Scan the library, then grant the gitlab-handbook sections to the demo groups (idempotent)."""
import asyncio, sys
sys.path.insert(0, ".")
from sqlalchemy import select
from app.config import get_settings
from app.db import session_factory, dispose
from app.ingest.library import scan_library
from app.models import Folder, Grant, Group
from app.services import build_services

EVERYONE = ["home", "about", "company", "communication", "tools-and-tips", "it", "business-technology",
            "customer-experience", "enterprise-data", "eta", "marketing", "sales", "product", "alliances",
            "resellers", "solutions-architects", "support", "job-description-library", "upstream-studios"]
OWNED = {
    "engineering": ["engineering", "security", "product-development"],
    "hr": ["people-group", "people-policies", "total-rewards", "hiring", "labor-and-employment-notices"],
    "finance": ["finance", "entity"],
    "leadership": ["leadership", "legal", "ceo", "eba", "board-meetings", "acquisitions"],
}
ROOT = "gitlab-handbook"

async def main():
    s = await build_services(get_settings(), session_factory())
    try:
        async with s.sessions() as db:
            print("scan:", await scan_library(db, s.vectors, s.settings.library_dir))
            folders = {f.path: f for f in (await db.scalars(select(Folder))).all()}
            groups = {g.name: g for g in (await db.scalars(select(Group))).all()}
            sections = sorted(p.split("/")[1] for p in folders if p.startswith(ROOT + "/") and p.count("/") == 1)
            planned = set(EVERYONE) | {x for v in OWNED.values() for x in v}
            missing = sorted(set(sections) - planned); extra = sorted(planned - set(sections))
            assert not missing and not extra, f"unmapped sections {missing}, unknown {extra}"
            wanted = []
            for sec in EVERYONE:
                wanted += [(sec, "everyone", "viewer"), (sec, "leadership", "viewer")]
            for group, secs in OWNED.items():
                for sec in secs:
                    wanted.append((sec, group, "editor"))
                    if group != "leadership":
                        wanted.append((sec, "leadership", "viewer"))
            have = {(g.folder_id, g.subject_type, g.subject_id, g.relation) for g in (await db.scalars(select(Grant))).all()}
            added = 0
            for sec, group, relation in wanted:
                key = (folders[f"{ROOT}/{sec}"].id, "group", groups[group].id, relation)
                if key not in have:
                    db.add(Grant(folder_id=key[0], subject_type="group", subject_id=key[2], relation=relation)); added += 1
            root_grants = [g for g in (await db.scalars(select(Grant).where(Grant.folder_id == folders[ROOT].id))).all()]
            assert not root_grants, "the gitlab-handbook root must stay ungranted"
            await db.commit()
            print(f"{len(sections)} sections, {added} grants added")
        print("authz sync:", await s.authz.sync())
    finally:
        await s.close(); await dispose()

asyncio.run(main())
