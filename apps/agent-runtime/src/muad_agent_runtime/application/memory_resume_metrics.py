"""Count durable decisions after the outer transaction commits."""

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

from ..metrics import MEMORY_RESUME_METRIC, record_counter


def committed_resume_metric(session: AsyncSession, outcome: str) -> None:
    pending = [outcome]
    transaction = session.sync_session.get_nested_transaction() or session.sync_session.get_transaction()

    @event.listens_for(session.sync_session, "after_commit")
    def committed(sync: Session) -> None:
        if not sync.in_nested_transaction() and pending:
            record_counter(MEMORY_RESUME_METRIC, labels={"outcome": pending.pop()})

    @event.listens_for(session.sync_session, "after_soft_rollback")
    def rolled_back(sync: Session, previous: SessionTransaction) -> None:
        owner = transaction
        while owner is not None:
            if owner is previous:
                pending.clear()
                return
            owner = owner.parent
