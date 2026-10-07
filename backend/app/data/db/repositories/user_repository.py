"""Canonical development membership provisioning, separate from feature stores."""

from sqlalchemy import Engine
from sqlalchemy.dialects.postgresql import insert

from app.data.db.models.user import memberships, workspaces
from app.utils.security import Identity


class DevelopmentIdentityRepository:
    def __init__(self, engine: Engine):
        self._engine = engine

    def provision(self, identity: Identity) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(workspaces).values(id=identity.workspace_id).on_conflict_do_nothing()
            )
            connection.execute(
                insert(memberships)
                .values(workspace_id=identity.workspace_id, subject=identity.subject, role="owner")
                .on_conflict_do_nothing()
            )
