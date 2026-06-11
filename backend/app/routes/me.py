from fastapi import APIRouter, Depends

from app.auth import get_current_user

router = APIRouter()


@router.get("/api/me")
async def me(user: dict = Depends(get_current_user)):
    return {
        "name": user.get("name"),
        "email": user.get("preferred_username"),
        "oid": user.get("oid"),
        "tenant": user.get("tid"),
    }
