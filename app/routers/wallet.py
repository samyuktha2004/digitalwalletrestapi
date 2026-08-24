from fastapi import APIRouter

from app.routers.deps import CurrentUser, DecryptedTransfer, SessionDep
from app.schemas import AmountIn, TransactionOut, WalletOut
from app.services import wallet_service

router = APIRouter(prefix="/wallet", tags=["wallet"])


@router.get("", response_model=WalletOut)
async def read_wallet(current_user: CurrentUser, session: SessionDep) -> WalletOut:
    wallet = await wallet_service.get_wallet(session, current_user.id)
    return WalletOut.model_validate(wallet)


@router.get("/transactions", response_model=list[TransactionOut])
async def read_transactions(
    current_user: CurrentUser, session: SessionDep, limit: int = 50
) -> list[TransactionOut]:
    wallet = await wallet_service.get_wallet(session, current_user.id)
    rows = await wallet_service.list_transactions(session, wallet.id, limit=min(limit, 200))
    return [TransactionOut.model_validate(r) for r in rows]


@router.post("/deposit", response_model=WalletOut)
async def deposit(body: AmountIn, current_user: CurrentUser, session: SessionDep) -> WalletOut:
    wallet = await wallet_service.deposit(session, current_user.id, body.amount)
    return WalletOut.model_validate(wallet)


@router.post("/withdraw", response_model=WalletOut)
async def withdraw(body: AmountIn, current_user: CurrentUser, session: SessionDep) -> WalletOut:
    wallet = await wallet_service.withdraw(session, current_user.id, body.amount)
    return WalletOut.model_validate(wallet)


@router.post("/transfer", response_model=WalletOut)
async def transfer(
    body: DecryptedTransfer, current_user: CurrentUser, session: SessionDep
) -> WalletOut:
    """Body is AES-256-GCM encrypted: {"payload": "<base64 iv>:<base64 ciphertext>"}."""
    wallet = await wallet_service.transfer(
        session, current_user.id, body.recipient_username, body.amount
    )
    return WalletOut.model_validate(wallet)
