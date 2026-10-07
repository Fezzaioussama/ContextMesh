"""Serialized turn claims and atomic execution-token-fenced completion."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, RowMapping, insert, select, update

from app.data.db.models.conversation import (
    conversations,
    messages,
    turns,
)
from app.data.db.repositories.access import (
    require_conversation,
)
from app.data.db.repositories.answer_records import require_visible_evidence, save_answer
from app.data.db.repositories.turn_values import (
    bounded_history,
    completed_result,
    turn_message,
)
from app.services.rules.errors import idempotency_conflict, turn_in_progress
from app.services.rules.models import (
    AgentOutcome,
    Execution,
    Message,
    TurnInput,
    TurnResult,
)
from app.utils.security import Identity


class TurnRepository:
    def __init__(self, engine: Engine, lease_seconds: int):
        self.engine = engine
        self.lease_seconds = lease_seconds

    def replay(
        self, identity: Identity, conversation_id: UUID, key: str, turn: TurnInput
    ) -> TurnResult | None:
        """A completed turn is returned even if its source scope has since changed."""
        with self.engine.begin() as connection:
            require_conversation(connection, identity, conversation_id)
            existing = self._existing(connection, conversation_id, key, turn.fingerprint)
            if existing is None or existing["status"] != "completed":
                return None
            return completed_result(connection, existing)

    def claim(
        self, identity: Identity, conversation_id: UUID, key: str, turn: TurnInput
    ) -> Execution | TurnResult:
        payload_hash = turn.fingerprint
        message = turn.message
        with self.engine.begin() as connection:
            require_conversation(connection, identity, conversation_id, lock=True)
            existing = self._existing(connection, conversation_id, key, payload_hash)
            if existing is not None and existing["status"] == "completed":
                return completed_result(connection, existing)
            now = datetime.now(UTC)
            self._expire_and_check(connection, conversation_id, now)
            turn_id = self._claim_row(
                connection, existing, conversation_id, key, payload_hash, message, now
            )
            return self._execution(connection, turn_id, conversation_id)

    def _existing(
        self, connection: Connection, conversation_id: UUID, key: str, payload_hash: str
    ) -> RowMapping | None:
        row = (
            connection.execute(
                select(turns).where(
                    turns.c.conversation_id == conversation_id, turns.c.idempotency_key == key
                )
            )
            .mappings()
            .first()
        )
        if row is not None and row["payload_hash"] != payload_hash:
            raise idempotency_conflict()
        return row

    def _expire_and_check(
        self, connection: Connection, conversation_id: UUID, now: datetime
    ) -> None:
        connection.execute(
            update(turns)
            .where(
                turns.c.conversation_id == conversation_id,
                turns.c.status == "running",
                turns.c.lease_until <= now,
            )
            .values(status="failed", safe_error_code="execution_expired")
        )
        running = connection.execute(
            select(turns.c.id).where(
                turns.c.conversation_id == conversation_id, turns.c.status == "running"
            )
        ).first()
        if running is not None:
            raise turn_in_progress()

    def _claim_row(
        self,
        connection: Connection,
        existing: RowMapping | None,
        conversation_id: UUID,
        key: str,
        payload_hash: str,
        message: str,
        now: datetime,
    ) -> UUID:
        values = {
            "execution_token": uuid4(),
            "lease_until": now + timedelta(seconds=self.lease_seconds),
            "status": "running",
            "safe_error_code": None,
        }
        if existing is not None:
            connection.execute(update(turns).where(turns.c.id == existing["id"]).values(**values))
            return existing["id"]
        turn_id = uuid4()
        connection.execute(
            insert(turns).values(
                id=turn_id,
                conversation_id=conversation_id,
                idempotency_key=key,
                payload_hash=payload_hash,
                **values,
            )
        )
        connection.execute(
            insert(messages).values(
                id=uuid4(),
                conversation_id=conversation_id,
                turn_id=turn_id,
                role="user",
                content=message,
                created_at=now,
            )
        )
        connection.execute(
            update(conversations)
            .where(conversations.c.id == conversation_id)
            .values(updated_at=now)
        )
        return turn_id

    def _execution(self, connection: Connection, turn_id: UUID, conversation_id: UUID) -> Execution:
        token = connection.execute(
            select(turns.c.execution_token).where(turns.c.id == turn_id)
        ).scalar_one()
        return Execution(
            turn_id,
            conversation_id,
            token,
            turn_message(connection, turn_id, "user"),
            bounded_history(connection, conversation_id, turn_id),
        )

    def complete(
        self, identity: Identity, execution: Execution, outcome: AgentOutcome
    ) -> TurnResult:
        with self.engine.begin() as connection:
            require_conversation(connection, identity, execution.conversation_id, lock=True)
            self._require_live_execution(connection, execution)
            require_visible_evidence(connection, outcome.answer)
            assistant = self._save_reply(connection, execution, outcome)
            return TurnResult(
                execution.turn_id,
                execution.conversation_id,
                execution.user_message,
                assistant,
                outcome.usage,
                outcome.trace,
            )

    def _require_live_execution(self, connection: Connection, execution: Execution) -> None:
        row = connection.execute(select(turns).where(turns.c.id == execution.turn_id))
        value = row.mappings().one()
        if (
            value["execution_token"] != execution.token
            or value["status"] != "running"
            or value["lease_until"] <= datetime.now(UTC)
        ):
            raise turn_in_progress()

    def _save_reply(
        self, connection: Connection, execution: Execution, outcome: AgentOutcome
    ) -> Message:
        now = datetime.now(UTC)
        content = outcome.answer.text
        message = Message(
            uuid4(), execution.turn_id, "assistant", content, now, outcome.answer, outcome.trace
        )
        connection.execute(
            insert(messages).values(
                id=message.id,
                conversation_id=execution.conversation_id,
                turn_id=execution.turn_id,
                role="assistant",
                content=content,
                created_at=now,
            )
        )
        save_answer(connection, message.id, outcome)
        connection.execute(
            update(turns)
            .where(turns.c.id == execution.turn_id)
            .values(
                status="completed",
                input_tokens=outcome.usage.input_tokens,
                output_tokens=outcome.usage.output_tokens,
            )
        )
        connection.execute(
            update(conversations)
            .where(conversations.c.id == execution.conversation_id)
            .values(updated_at=now)
        )
        return message

    def fail(self, identity: Identity, execution: Execution, code: str) -> None:
        with self.engine.begin() as connection:
            require_conversation(connection, identity, execution.conversation_id, lock=True)
            connection.execute(
                update(turns)
                .where(
                    turns.c.id == execution.turn_id,
                    turns.c.execution_token == execution.token,
                    turns.c.status == "running",
                )
                .values(status="failed", safe_error_code=code[:50])
            )
