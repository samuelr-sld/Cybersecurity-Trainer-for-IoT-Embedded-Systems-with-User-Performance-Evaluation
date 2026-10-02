"""HTTP endpoints for participants and the Evaluation page.

    POST /api/participants            register {participant_id, full_name}
    POST /api/participants/sign-in    sign in  {participant_id}  -> stored participant
    GET  /api/participants            registered participants + session counts
    GET  /api/evaluation/{id}         one participant's sessions and metrics

Plain request/response over the existing FastAPI app — the WebSocket
protocols are untouched. Responses carry only participant identity, session
headers, event-type names, attempt counts and `MetricValue`s: no command
arguments, broker credentials, lab secrets or firmware content are read or
returned here (see `app/evaluation.py`, which is the only data source).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from app.build_sessions import build_session_manager
from app.evaluation import EvaluationService
from app.events.store import StoreError
from app.participants import (
    ParticipantError,
    ParticipantExistsError,
    ParticipantNotFoundError,
    ParticipantService,
)
from app.sessions import session_manager

router = APIRouter(prefix="/api")


class SignInBody(BaseModel):
    """Sign-in names a student number and nothing else."""

    model_config = ConfigDict(extra="forbid")

    participant_id: str


class ParticipantBody(SignInBody):
    """Registration is the one place a full name is supplied."""

    full_name: str


def _evaluation() -> EvaluationService:
    return EvaluationService(
        is_hack_live=session_manager.is_live,
        is_build_live=build_session_manager.is_live,
    )


def _unavailable(error: StoreError) -> HTTPException:
    return HTTPException(status_code=503, detail="evaluation store unavailable")


@router.post("/participants", status_code=201)
def register_participant(body: ParticipantBody) -> dict:
    try:
        record = ParticipantService().register(body.participant_id, body.full_name)
    except ParticipantExistsError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ParticipantError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except StoreError as error:
        raise _unavailable(error) from error
    return record.to_payload()


@router.post("/participants/sign-in")
def sign_in_participant(body: SignInBody) -> dict:
    try:
        record = ParticipantService().sign_in(body.participant_id)
    except ParticipantNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except StoreError as error:
        raise _unavailable(error) from error
    return record.to_payload()


@router.get("/participants")
def list_participants() -> dict:
    try:
        service = _evaluation()
        return {
            "participants": [service.summary(p) for p in ParticipantService().all()]
        }
    except StoreError as error:
        raise _unavailable(error) from error


@router.get("/evaluation/{participant_id}")
def participant_evaluation(participant_id: str) -> dict:
    try:
        record = ParticipantService().get(participant_id)
        if record is None:
            raise HTTPException(status_code=404, detail="no such registered participant")
        return _evaluation().report(record)
    except StoreError as error:
        raise _unavailable(error) from error
