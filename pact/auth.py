import hashlib
import secrets
from dataclasses import dataclass

from fastapi import HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials


@dataclass(frozen=True)
class Identity:
    tenant: str
    subject: str
    role: str


def identity(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False))) -> Identity:
    settings = request.app.state.settings
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")
    token = header[7:]
    if settings.mode in {"demo", "test"}:
        for role in ["reader", "reviewer", "admin"]:
            if secrets.compare_digest(token, f"demo-{role}"):
                return Identity("demo", f"demo-{role}", role)
    digest = hashlib.sha256(token.encode()).hexdigest()
    record = settings.api_keys.get(digest)
    if not record or record.get("role") not in {"reader", "reviewer", "admin"}:
        raise HTTPException(401, "Invalid credentials")
    return Identity(record["tenant"], record["subject"], record["role"])


def require(who: Identity, *roles: str):
    if who.role not in roles:
        raise HTTPException(403, "This action requires a different role")
