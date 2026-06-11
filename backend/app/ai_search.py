"""
Azure AI Search client for RAG.

Hybrid retrieval (vector + BM25) with optional semantic reranker.
The index is configured with an Azure OpenAI vectorizer, so the search
service embeds the user's query server-side — we just pass plain text.

Auth: managed identity (UAMI) via DefaultAzureCredential when running in
Azure (AZURE_CLIENT_ID env var), or AzureKeyCredential when an API key
is provided (only useful for local dev against a key-enabled service;
the default deployment disables local auth).

Falls back to the in-memory TF-IDF knowledge base if Search is not
configured (no AZURE_SEARCH_ENDPOINT set).
"""

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "")
SEARCH_INDEX_NAME = os.getenv("AZURE_SEARCH_INDEX_NAME", "rag-index")
SEARCH_API_KEY = os.getenv("AZURE_SEARCH_API_KEY", "")
SEARCH_SEMANTIC_CONFIG = os.getenv("AZURE_SEARCH_SEMANTIC_CONFIG", "")
SEARCH_VECTOR_FIELD = os.getenv("AZURE_SEARCH_VECTOR_FIELD", "text_vector")


def is_search_configured() -> bool:
    return bool(SEARCH_ENDPOINT)


class AISearchClient:
    def __init__(self):
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient
        from azure.identity import DefaultAzureCredential

        if not SEARCH_ENDPOINT:
            raise ValueError("AZURE_SEARCH_ENDPOINT is required")

        if SEARCH_API_KEY:
            logger.info("Using API key auth for Azure AI Search")
            credential = AzureKeyCredential(SEARCH_API_KEY)
        else:
            logger.info("Using managed identity auth for Azure AI Search")
            credential = DefaultAzureCredential()

        self.client = SearchClient(
            endpoint=SEARCH_ENDPOINT,
            index_name=SEARCH_INDEX_NAME,
            credential=credential,
        )
        self.semantic_config = SEARCH_SEMANTIC_CONFIG
        logger.info(
            "AI Search client initialized: endpoint=%s index=%s semantic=%s vector_field=%s",
            SEARCH_ENDPOINT,
            SEARCH_INDEX_NAME,
            self.semantic_config or "disabled",
            SEARCH_VECTOR_FIELD,
        )

    def search(self, query: str, top: int = 3, filter_expr: str = "") -> list[dict]:
        """
        Hybrid search: vector (via index-side vectorizer) + BM25 keyword.

        `filter_expr` is an OData filter passed straight to AI Search. The
        partner-aware caller uses this to scope results to a single
        partner's documents, e.g. `partner eq 'acme'`. Empty string means
        no filter (admin/internal callers see everything).

        If the index has no vector field (legacy schema) the SDK still
        accepts the request — the vector_queries are just ignored when
        the field is missing. We try hybrid first, then fall back to
        plain text if the SDK rejects the call.
        """
        from azure.search.documents.models import VectorizableTextQuery

        # We don't pass `select` — different index schemas use different
        # field names (the integrated-vectorization template uses
        # `chunk`; older hand-rolled schemas often used `content`).
        # Selecting a field that doesn't exist makes Search reject the
        # entire query with `Could not find a property named '<name>'
        # on type 'search.document'`. Letting Search return all
        # retrievable fields and coalescing in _format_results is the
        # robust option.
        common_kwargs: dict = {
            "search_text": query,
            "top": top,
        }
        if filter_expr:
            common_kwargs["filter"] = filter_expr

        if self.semantic_config:
            common_kwargs["query_type"] = "semantic"
            common_kwargs["semantic_configuration_name"] = self.semantic_config
            # Semantic captions/answers improve answer quality but cost
            # extra. Enable captions; skip answers (we re-summarize via
            # the LLM anyway).
            common_kwargs["query_caption"] = "extractive"

        # Build a vector query that the search service will embed using
        # the index's configured vectorizer. Same text as the keyword
        # search — that's the standard hybrid pattern.
        vector_query = VectorizableTextQuery(
            text=query,
            k_nearest_neighbors=max(top * 5, 20),
            fields=SEARCH_VECTOR_FIELD,
        )

        try:
            results = self.client.search(
                vector_queries=[vector_query],
                **common_kwargs,
            )
            documents = self._format_results(results)
            logger.info("Hybrid search returned %d results for: %s", len(documents), query)
            return documents
        except Exception as e:
            # Older indexes without a vector field will reject vector
            # queries. Fall back to keyword-only.
            logger.warning("Hybrid search failed (%s); falling back to keyword.", e)
            try:
                results = self.client.search(**common_kwargs)
                documents = self._format_results(results)
                logger.info("Keyword search returned %d results for: %s", len(documents), query)
                return documents
            except Exception as e2:
                logger.error("AI Search failed: %s", e2, exc_info=True)
                raise

    @staticmethod
    def _format_results(results) -> list[dict]:
        out = []
        for r in results:
            out.append({
                "title":   r.get("title") or "",
                "content": r.get("chunk") or r.get("content") or "",
                "source":  r.get("source") or "",
                "score":   r.get("@search.score", 0),
            })
        return out


# Singleton
_search_client: Optional[AISearchClient] = None


def get_search_client() -> AISearchClient:
    global _search_client
    if _search_client is None:
        _search_client = AISearchClient()
    return _search_client


def search(query: str, top_k: int = 3, filter_expr: str = "") -> list[dict]:
    """
    Search using Azure AI Search.
    Returns results in the same format as knowledge_base.search().

    `filter_expr` is an OData filter (e.g. "partner eq 'acme'"). Empty
    string means no filter.
    """
    client = get_search_client()
    return client.search(query=query, top=top_k, filter_expr=filter_expr)
