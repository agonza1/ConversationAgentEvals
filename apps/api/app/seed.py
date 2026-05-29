from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from app.db.database import Base, SessionLocal, engine
from app.models import entities  # noqa: F401 - registers SQLAlchemy models
from app.models.entities import ProductProject, ProductWorkspace, ProductWorkspaceMember
from app.services.benchmark_service import list_suites

DEMO_USER_ID = 'docker-demo-user'
DEMO_WORKSPACE_KEY = 'docker-local-qa'


def seed_demo_data() -> dict[str, int]:
    Base.metadata.create_all(bind=engine)
    created = {'workspaces': 0, 'members': 0, 'projects': 0}

    with SessionLocal() as db:
        workspace = (
            db.query(ProductWorkspace)
            .filter(
                ProductWorkspace.owner_user_id == DEMO_USER_ID,
                ProductWorkspace.workspace_key == DEMO_WORKSPACE_KEY,
            )
            .one_or_none()
        )
        if workspace is None:
            workspace = ProductWorkspace(
                owner_user_id=DEMO_USER_ID,
                workspace_key=DEMO_WORKSPACE_KEY,
                name='Docker Local QA',
                plan='team',
                settings_json='{"default_benchmark_suite": "call-center-voice-ai"}',
                onboarding_json='{"seeded_by": "docker-compose"}',
            )
            db.add(workspace)
            db.flush()
            created['workspaces'] += 1

        member = (
            db.query(ProductWorkspaceMember)
            .filter(
                ProductWorkspaceMember.workspace_id == workspace.id,
                ProductWorkspaceMember.user_id == DEMO_USER_ID,
            )
            .one_or_none()
        )
        if member is None:
            db.add(ProductWorkspaceMember(workspace_id=workspace.id, user_id=DEMO_USER_ID, role='owner'))
            created['members'] += 1

        for suite in list_suites():
            project_key = str(suite['id'])
            project = (
                db.query(ProductProject)
                .filter(ProductProject.user_id == DEMO_USER_ID, ProductProject.project_key == project_key)
                .one_or_none()
            )
            if project is None:
                db.add(
                    ProductProject(
                        user_id=DEMO_USER_ID,
                        workspace_id=workspace.id,
                        project_key=project_key,
                        name=f"{suite['name']} Demo",
                        plan='team',
                        settings_json='{"source": "docker-seed"}',
                        onboarding_json='{"next_step": "run_benchmark"}',
                    )
                )
                created['projects'] += 1

        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise

    return created


def main() -> None:
    created = seed_demo_data()
    print(
        'Seeded Docker demo data: '
        f"{created['workspaces']} workspaces, {created['members']} members, {created['projects']} projects."
    )


if __name__ == '__main__':
    main()
