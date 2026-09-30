"""Endpoints for node agents. They authenticate with VM identity (register) and then with
their per-node agent token; user sessions are not accepted here."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_platform, get_session
from app.api.v1.schemas import (
    AgentCommandOut,
    AgentCommandResultRequest,
    AgentHeartbeatRequest,
    AgentHeartbeatResponse,
    AgentRegisterRequest,
    AgentRegisterResponse,
)
from app.application.agent_service import AgentService
from app.application.platform import Platform
from app.domain.errors import AuthenticationFailed

router = APIRouter(prefix="/agent", tags=["agent"])


def _agent_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer ") or not header[7:].strip():
        raise AuthenticationFailed("Agent token required.")
    return header[7:].strip()


@router.post("/register", response_model=AgentRegisterResponse)
def register(
    body: AgentRegisterRequest,
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> AgentRegisterResponse:
    token, node = AgentService(session, platform).register(
        body.cluster_id, body.node_name, body.identity_token, body.agent_version
    )
    return AgentRegisterResponse(
        agent_token=token,
        node_id=node.id,
        heartbeat_interval_seconds=platform.settings.agent_heartbeat_interval_seconds,
    )


@router.post("/heartbeat", response_model=AgentHeartbeatResponse)
def heartbeat(
    body: AgentHeartbeatRequest,
    request: Request,
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> AgentHeartbeatResponse:
    commands = AgentService(session, platform).heartbeat(_agent_token(request), body.report)
    return AgentHeartbeatResponse(
        commands=[AgentCommandOut(id=c.id, command=c.command, args=c.args or {}) for c in commands],
        heartbeat_interval_seconds=platform.settings.agent_heartbeat_interval_seconds,
    )


@router.post("/commands/{command_id}/result", status_code=204)
def command_result(
    command_id: uuid.UUID,
    body: AgentCommandResultRequest,
    request: Request,
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> Response:
    AgentService(session, platform).command_result(
        _agent_token(request), command_id, body.status == "succeeded", body.message, body.output
    )
    return Response(status_code=204)
