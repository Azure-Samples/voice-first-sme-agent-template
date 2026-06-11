import os

from dotenv import load_dotenv

load_dotenv()

STAGE = os.getenv("STAGE", "dev")
REQUIRE_AUTH = os.getenv("REQUIRE_AUTH", "true").lower() == "true"
ENABLE_DOCS = os.getenv("ENABLE_DOCS", "false").lower() == "true"
# Exposes the OpenAPI JSON spec at /openapi.json independently of the
# interactive Swagger UI (`ENABLE_DOCS`). Enabled in deployments that
# are onboarded to URSA Web Scanner so the scanner can enumerate
# endpoints. The spec itself only describes routes; every documented
# endpoint enforces its own auth — exposing the spec does not weaken
# any auth boundary. Keep /docs and /redoc HTML viewers off in prod
# (set ENABLE_DOCS=false) to minimize attack surface.
EXPOSE_OPENAPI_SPEC = os.getenv("EXPOSE_OPENAPI_SPEC", "false").lower() == "true"

MSAL_CLIENT_ID = os.getenv("MSAL_CLIENT_ID", "")
MSAL_TENANT_ID = os.getenv("MSAL_TENANT_ID", "")

# External vendor (Tribe) tenant
MSAL_TRIBE_CLIENT_ID = os.getenv("MSAL_TRIBE_CLIENT_ID", "")
MSAL_TRIBE_TENANT_ID = os.getenv("MSAL_TRIBE_TENANT_ID", "")

# Azure Voice Live
VOICELIVE_ENDPOINT = os.getenv("AZURE_VOICELIVE_ENDPOINT", "").rstrip("/")
VOICELIVE_API_KEY = os.getenv("AZURE_VOICELIVE_API_KEY", "")
VOICELIVE_API_VERSION = os.getenv("AZURE_VOICELIVE_API_VERSION", "2025-10-01")
VOICELIVE_MODEL = os.getenv("AZURE_VOICELIVE_MODEL", "gpt-5.2")
VOICELIVE_VOICE = os.getenv("AZURE_VOICELIVE_VOICE", "en-US-Andrew:DragonHDLatestNeural")
PERSONAL_VOICE_PROFILE_ID = os.getenv("AZURE_PERSONAL_VOICE_PROFILE_ID", "")
# Auth mode: 'key' (legacy, uses VOICELIVE_API_KEY) or 'mi' (Microsoft
# Entra via DefaultAzureCredential — requires the UAMI to have
# Cognitive Services User on the Voice Live account).
VOICELIVE_AUTH_MODE = os.getenv("AZURE_VOICELIVE_AUTH_MODE", "key").lower()


def get_voicelive_bearer_token() -> str:
    """Fetch a Bearer token for the Voice Live (Cognitive Services) API.

    Used when VOICELIVE_AUTH_MODE == 'mi'. The Container App's UAMI must
    have 'Cognitive Services User' on the Voice Live account. Tokens
    are valid for ~24h; callers fetch a fresh one per outbound
    connection so no refresh logic is needed.
    """
    from azure.identity import DefaultAzureCredential

    cred = DefaultAzureCredential()
    return cred.get_token("https://cognitiveservices.azure.com/.default").token

# Agent variant — override system prompt and knowledge base from external files
SYSTEM_PROMPT_FILE = os.getenv("SYSTEM_PROMPT_FILE", "")
KNOWLEDGE_BASE_FILE = os.getenv("KNOWLEDGE_BASE_FILE", "")

# Partner registry — drives /login allow-list and per-partner AI Search filtering
PARTNERS_FILE = os.getenv("PARTNERS_FILE", "")

# Cosmos DB transcript sink — feature is enabled when all four are set.
# Backend uses MSI (DefaultAzureCredential) to authenticate; no keys.
COSMOS_ENDPOINT = os.getenv("AZURE_COSMOS_ENDPOINT", "").rstrip("/")
COSMOS_DATABASE = os.getenv("AZURE_COSMOS_DATABASE", "")
COSMOS_CONTAINER = os.getenv("AZURE_COSMOS_CONTAINER", "")
# Per-deployment salt (base64) used to hash user identifiers before
# persisting transcripts. See app/transcripts.py.
TRANSCRIPT_SALT_BASE64 = os.getenv("TRANSCRIPT_SALT_BASE64", "")
