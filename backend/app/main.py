import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import STAGE, ENABLE_DOCS, EXPOSE_OPENAPI_SPEC, REQUIRE_AUTH
from app.routes.health import router as health_router
from app.routes.me import router as me_router
from app.routes.transcripts import router as transcripts_router
from app.routes.voice import router as voice_router, init_voice_audio
from app.security_headers import SecurityHeadersMiddleware
from app.transcripts import close_sink as close_transcript_sink

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not REQUIRE_AUTH:
        logger.warning("REQUIRE_AUTH=false — auth is BYPASSED. Do not run with auth disabled in production.")
    try:
        init_voice_audio()
    except Exception:
        logger.exception("Voice audio pre-generation failed; continuing startup without cached audio")
    try:
        yield
    finally:
        await close_transcript_sink()


app = FastAPI(
    title="Agent P API",
    lifespan=lifespan,
    docs_url="/docs" if ENABLE_DOCS else None,
    redoc_url="/redoc" if ENABLE_DOCS else None,
    # The OpenAPI JSON spec is exposed when either the Swagger UI is
    # on (dev convenience) or when the deployment opts into URSA Web
    # Scanner onboarding (EXPOSE_OPENAPI_SPEC=true).
    openapi_url="/openapi.json" if (ENABLE_DOCS or EXPOSE_OPENAPI_SPEC) else None,
)

# TODO: When we have custom domains, we need to add them to the allow_origins list
allow_origins: list[str] = []
if STAGE == "dev":
    allow_origins.append("http://localhost:5173")


app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(SecurityHeadersMiddleware)

app.include_router(health_router)
app.include_router(me_router)
app.include_router(transcripts_router)
app.include_router(voice_router)

# In prod, serve the built Vite frontend static files
static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.isdir(static_dir):
    # SPA fallback for /tribe and /login paths — serve the same index.html
    # so the React app can pick up the path and route auth accordingly.
    index_html = os.path.join(static_dir, "index.html")

    @app.get("/tribe")
    @app.get("/tribe/{rest:path}")
    async def tribe_spa_fallback(rest: str = ""):
        return FileResponse(index_html)

    @app.get("/login")
    @app.get("/login/{rest:path}")
    async def login_spa_fallback(rest: str = ""):
        return FileResponse(index_html)

    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
