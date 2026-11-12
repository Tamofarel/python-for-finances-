from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_db, get_state
from app.core.state import AppState
from app.models import User
from app.schemas import ProviderSelection, ProvidersOut, ProviderStatusOut
from app.services import audit
from app.services.assistant import SELECTED_PROVIDER_KEY, get_setting, set_setting

router = APIRouter(prefix="/api/providers", tags=["providers"])


def _selection(db: Session, user: User, state: AppState) -> ProviderSelection:
    sel = get_setting(db, user, SELECTED_PROVIDER_KEY)
    if isinstance(sel, dict) and sel.get("provider") in state.providers.names():
        return ProviderSelection(provider=str(sel["provider"]), model=str(sel.get("model") or "") or "default")
    p = state.providers.get(state.settings.default_provider)
    return ProviderSelection(provider=p.name, model=p.default_model())


@router.get("", response_model=ProvidersOut)
async def list_providers(user: User = Depends(current_user), db: Session = Depends(get_db),
                         state: AppState = Depends(get_state)) -> ProvidersOut:
    statuses = await state.providers.statuses()  # live checks; nothing is assumed connected
    return ProvidersOut(providers=[ProviderStatusOut(**asdict(s)) for s in statuses],
                        selected=_selection(db, user, state))


@router.put("/selection", response_model=ProviderSelection)
def select_provider(body: ProviderSelection, user: User = Depends(current_user), db: Session = Depends(get_db),
                    state: AppState = Depends(get_state)) -> ProviderSelection:
    state.providers.get(body.provider)  # validates the name
    set_setting(db, user, SELECTED_PROVIDER_KEY, body.model_dump())
    audit.record(db, "provider.selected", user_id=user.id, target=f"{body.provider}:{body.model}")
    return body
