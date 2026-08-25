import uuid
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Transaction, TransactionStatus, TransactionType, User, Wallet
from app.services.errors import BadRequest, InsufficientFunds, NotFound


async def get_wallet(session: AsyncSession, user_id: uuid.UUID) -> Wallet:
    wallet = await session.scalar(select(Wallet).where(Wallet.user_id == user_id))
    if wallet is None:
        raise NotFound("Wallet not found")
    return wallet


async def list_transactions(
    session: AsyncSession, wallet_id: uuid.UUID, limit: int = 50
) -> Sequence[Transaction]:
    return (
        await session.scalars(
            select(Transaction)
            .where(Transaction.wallet_id == wallet_id)
            .order_by(Transaction.created_at.desc(), Transaction.id.desc())
            .limit(limit)
        )
    ).all()


async def _lock_wallet_by_user(session: AsyncSession, user_id: uuid.UUID) -> Wallet:
    """SELECT ... FOR UPDATE — serialises concurrent writers on this row.

    populate_existing is not optional: without it the ORM takes the row lock but
    still hands back whatever copy of the row is already in the session's
    identity map, so the balance you read predates the lock and concurrent
    writers silently lose updates.

    ponytail: an atomic `UPDATE ... WHERE balance >= :amt RETURNING` would avoid
    the lock entirely and be faster; pessimistic locking is used because
    transfer needs to hold two rows consistent, and one mechanism beats two.
    """
    wallet = await session.scalar(
        select(Wallet)
        .where(Wallet.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if wallet is None:
        raise NotFound("Wallet not found")
    return wallet


async def _lock_wallets_ordered(
    session: AsyncSession, wallet_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Wallet]:
    """Lock several wallets in a globally consistent order (ascending id).

    Two simultaneous A->B and B->A transfers would otherwise each hold the row
    the other needs. Locks are taken one statement at a time rather than one
    `ORDER BY ... FOR UPDATE`, because Postgres does not promise it acquires
    row locks in the query's sort order.
    """
    locked: dict[uuid.UUID, Wallet] = {}
    for wallet_id in sorted(wallet_ids):
        wallet = await session.scalar(
            select(Wallet)
            .where(Wallet.id == wallet_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if wallet is None:
            raise NotFound("Wallet not found")
        locked[wallet_id] = wallet
    return locked


def _log(
    wallet_id: uuid.UUID,
    tx_type: TransactionType,
    amount: Decimal,
    recipient_wallet_id: uuid.UUID | None = None,
    status: TransactionStatus = TransactionStatus.SUCCESS,
) -> Transaction:
    return Transaction(
        wallet_id=wallet_id,
        type=tx_type,
        amount=amount,
        recipient_wallet_id=recipient_wallet_id,
        status=status,
    )


async def _record_refusal(
    session: AsyncSession,
    wallet_id: uuid.UUID,
    tx_type: TransactionType,
    amount: Decimal,
    recipient_wallet_id: uuid.UUID | None = None,
) -> None:
    """Persist a FAILED row for an attempt that is about to be refused.

    A rejected debit is the fraud-relevant event in a wallet — repeated
    insufficient-funds attempts are exactly what an audit trail is for — so it
    has to outlive the refusal that produced it.

    The subtlety is that the audit row cannot ride along on the caller's
    transaction: raising rolls that back and would take the row with it. It does
    not need a second session either. Everything the refusal path did was a
    locked *read*, so `rollback()` discards no work; it just releases the row
    locks. The audit row then commits in its own fresh transaction.

    Callers must read `wallet.id` into an argument *before* this runs — rollback
    expires every ORM object, and re-reading an expired attribute would emit
    lazy IO. Python evaluates the call arguments first, which is what makes
    `_record_refusal(session, wallet.id, ...)` safe.
    """
    await session.rollback()
    session.add(_log(wallet_id, tx_type, amount, recipient_wallet_id, TransactionStatus.FAILED))
    await session.commit()


async def deposit(session: AsyncSession, user_id: uuid.UUID, amount: Decimal) -> Wallet:
    # Locked too: balance += amount is read-modify-write and races the same way
    # a withdrawal does, it just loses money instead of overdrawing.
    wallet = await _lock_wallet_by_user(session, user_id)
    wallet.balance += amount
    session.add(_log(wallet.id, TransactionType.DEPOSIT, amount))
    await session.commit()
    return wallet


async def withdraw(session: AsyncSession, user_id: uuid.UUID, amount: Decimal) -> Wallet:
    wallet = await _lock_wallet_by_user(session, user_id)
    if wallet.balance < amount:
        await _record_refusal(session, wallet.id, TransactionType.WITHDRAWAL, amount)
        raise InsufficientFunds()
    wallet.balance -= amount
    session.add(_log(wallet.id, TransactionType.WITHDRAWAL, amount))
    await session.commit()
    return wallet


async def transfer(
    session: AsyncSession, sender_id: uuid.UUID, recipient_username: str, amount: Decimal
) -> Wallet:
    recipient = await session.scalar(select(User).where(User.username == recipient_username))
    if recipient is None:
        raise NotFound("Recipient not found")
    if recipient.id == sender_id:
        raise BadRequest("Cannot transfer to yourself")

    sender_wallet = await get_wallet(session, sender_id)
    recipient_wallet = await get_wallet(session, recipient.id)

    # Re-read both rows under lock; the unlocked reads above were only to
    # resolve ids, their balances are already stale by this point.
    locked = await _lock_wallets_ordered(session, [sender_wallet.id, recipient_wallet.id])
    sender_wallet = locked[sender_wallet.id]
    recipient_wallet = locked[recipient_wallet.id]

    if sender_wallet.balance < amount:
        await _record_refusal(
            session,
            sender_wallet.id,
            TransactionType.TRANSFER_OUT,
            amount,
            recipient_wallet.id,
        )
        raise InsufficientFunds()

    sender_wallet.balance -= amount
    recipient_wallet.balance += amount
    session.add_all(
        [
            _log(sender_wallet.id, TransactionType.TRANSFER_OUT, amount, recipient_wallet.id),
            _log(recipient_wallet.id, TransactionType.TRANSFER_IN, amount, sender_wallet.id),
        ]
    )
    await session.commit()
    return sender_wallet
