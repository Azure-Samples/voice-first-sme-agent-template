"""
Knowledge base for Voice Agent – Azure AI Foundry coaching.
In-memory TF-IDF search over curated Foundry documentation.

If KNOWLEDGE_BASE_FILE is set, documents are loaded from that JSON file instead.

Replace with real Azure AI Search when ready:
  from azure.search.documents.aio import SearchClient
  from azure.core.credentials import AzureKeyCredential
"""

import json
import math
import os
import re
from collections import Counter

from app.config import KNOWLEDGE_BASE_FILE


DOCUMENTS = [
    {
        "title": "Azure AI Foundry Overview",
        "content": (
            "Azure AI Foundry is Microsoft's unified platform for building, evaluating, and deploying "
            "AI applications at enterprise scale. It brings together model hosting, prompt engineering, "
            "evaluation, and deployment into a single experience. The platform includes the Azure AI Foundry "
            "portal (a web-based IDE for AI development), the Azure AI Foundry SDK (Python SDK for "
            "programmatic access), and integrations with Azure AI Search, Azure OpenAI, and other Azure "
            "services. AI Foundry is designed for developers, data scientists, and AI engineers who want "
            "to build production-ready AI applications with built-in responsible AI guardrails."
        ),
    },
    {
        "title": "Azure AI Foundry Projects and Hubs",
        "content": (
            "Azure AI Foundry organizes work into Projects and Hubs. A Hub is a top-level resource that "
            "provides shared infrastructure including compute, storage, and security policies. Projects "
            "live inside Hubs and represent individual AI applications or workstreams. Each project gets "
            "its own set of deployments, evaluation runs, and prompt flow assets. Hubs enable governance: "
            "admins set policies at the Hub level (networking, allowed models, cost controls) while "
            "developers work freely within their projects. Best practice: create one Hub per team or "
            "department, and one Project per application or use case."
        ),
    },
    {
        "title": "Azure AI Foundry Model Catalog",
        "content": (
            "The Model Catalog in Azure AI Foundry provides access to hundreds of models from Microsoft, "
            "OpenAI, Meta, Mistral, Cohere, and other providers. Models are available through three "
            "deployment options: Models as a Service (MaaS, pay-per-token, no infrastructure), Managed "
            "Compute (dedicated hosting with autoscaling), and Serverless API (pay-per-call for select "
            "models). Key model families include GPT-4o and GPT-4.1 (OpenAI), Phi-4 and MAI (Microsoft), "
            "Llama 4 (Meta), and Mistral Large. The catalog includes benchmarks, pricing, and capability "
            "comparisons to help choose the right model for each use case."
        ),
    },
    {
        "title": "Prompt Flow in Azure AI Foundry",
        "content": (
            "Prompt Flow is a visual development tool in Azure AI Foundry for building LLM-powered "
            "workflows. It lets you chain together prompts, Python code, and tool calls into a DAG "
            "(directed acyclic graph). Each node can be a prompt template, a Python function, or a "
            "connection to external services like Azure AI Search. Prompt Flow supports variants "
            "(A/B testing different prompts), batch evaluation against test datasets, and deployment "
            "as a managed endpoint. It is ideal for building RAG pipelines, multi-step reasoning "
            "chains, and complex AI workflows that need to be tested and iterated on quickly."
        ),
    },
    {
        "title": "RAG with Azure AI Search and Foundry",
        "content": (
            "Retrieval-Augmented Generation (RAG) is the most common pattern for grounding AI responses "
            "in enterprise data. In Azure AI Foundry, RAG typically combines Azure AI Search (for "
            "retrieval) with an LLM (for generation). Azure AI Search supports vector search, semantic "
            "ranking, and hybrid search (combining keyword + vector). To build a RAG app: 1) Index your "
            "documents in Azure AI Search with vector embeddings, 2) At query time, retrieve the top-k "
            "relevant chunks, 3) Pass them as context to the LLM along with the user's question. "
            "Azure AI Foundry provides built-in data connections to Azure AI Search, making it easy to "
            "set up grounding. Best practice: use hybrid search (keyword + vector) with semantic reranking "
            "for the best retrieval quality."
        ),
    },
    {
        "title": "Azure AI Foundry Evaluation",
        "content": (
            "Azure AI Foundry includes built-in evaluation tools for measuring AI application quality. "
            "Evaluations can assess groundedness (are responses based on provided context?), relevance "
            "(does the response answer the question?), coherence (is the response well-structured?), "
            "fluency, and safety (does the response contain harmful content?). You can run manual "
            "evaluations in the portal or automated evaluations via the SDK using test datasets. "
            "The evaluation framework supports custom metrics defined in Python. Best practice: "
            "create a golden test set of question-answer pairs and run evaluations on every prompt "
            "or model change to catch regressions early."
        ),
    },
    {
        "title": "Azure AI Foundry SDK",
        "content": (
            "The Azure AI Foundry SDK (azure-ai-projects) provides Python APIs for interacting with "
            "AI Foundry programmatically. Key capabilities include: creating and managing projects, "
            "deploying models, running evaluations, executing prompt flows, and managing connections "
            "to data sources. The SDK also includes the azure-ai-inference package for calling deployed "
            "models with a unified API regardless of the model provider. Example: "
            "from azure.ai.projects import AIProjectClient; client = AIProjectClient(endpoint, credential); "
            "response = client.inference.chat_completions(model='gpt-4o', messages=[...]). "
            "The SDK supports async operations and integrates with Azure Identity for authentication."
        ),
    },
    {
        "title": "Azure AI Agents in Foundry",
        "content": (
            "Azure AI Foundry provides an Agents API for building AI agents that can use tools, maintain "
            "conversation state, and execute multi-step tasks. Agents can be configured with instructions "
            "(system prompts), tools (functions, code interpreter, file search), and knowledge sources. "
            "The Agents API handles conversation threading, tool call orchestration, and state management. "
            "Agents can use built-in tools like Code Interpreter (runs Python in a sandbox), File Search "
            "(searches uploaded documents), and custom function tools. Best practice: start with a simple "
            "agent with one or two tools, then add complexity as needed. Use the evaluation framework "
            "to test agent behavior across different scenarios."
        ),
    },
    {
        "title": "Deploying AI Applications from Azure AI Foundry",
        "content": (
            "Azure AI Foundry supports multiple deployment targets for AI applications. Models can be "
            "deployed as managed online endpoints with autoscaling, or as serverless APIs for pay-per-call "
            "pricing. Prompt Flows can be deployed as managed endpoints that handle the full RAG pipeline. "
            "For custom applications, you can containerize your app and deploy to Azure Container Apps "
            "or Azure Kubernetes Service. Azure AI Foundry integrates with Azure DevOps and GitHub Actions "
            "for CI/CD pipelines. Best practice: use managed endpoints for model serving to get automatic "
            "scaling, monitoring, and versioning. Use blue-green deployments to safely roll out changes."
        ),
    },
    {
        "title": "Responsible AI in Azure AI Foundry",
        "content": (
            "Azure AI Foundry includes built-in responsible AI capabilities. Content Safety filters "
            "automatically detect and block harmful content in both inputs and outputs. You can configure "
            "filter severity levels for categories like hate speech, violence, sexual content, and "
            "self-harm. The platform supports red teaming (automated adversarial testing) to identify "
            "vulnerabilities before deployment. Groundedness detection helps prevent hallucinations by "
            "verifying that responses are grounded in provided context. Best practice: enable content "
            "safety filters from day one, run red teaming evaluations before any production deployment, "
            "and implement human review workflows for high-stakes applications."
        ),
    },
    {
        "title": "Azure OpenAI Service in AI Foundry",
        "content": (
            "Azure OpenAI Service provides access to OpenAI models (GPT-4o, GPT-4.1, o3, DALL-E, Whisper) "
            "with enterprise features: data privacy (your data is not used for training), regional "
            "deployment, virtual network support, and managed identity authentication. In AI Foundry, "
            "Azure OpenAI models appear in the model catalog and can be deployed to your project with "
            "a few clicks. Azure OpenAI supports structured outputs (JSON mode), function calling, "
            "vision (image understanding), and real-time audio (voice conversations). Provisioned "
            "throughput units (PTUs) provide guaranteed capacity for production workloads."
        ),
    },
    {
        "title": "Fine-Tuning Models in Azure AI Foundry",
        "content": (
            "Azure AI Foundry supports fine-tuning for select models including GPT-4o-mini, GPT-4o, "
            "Phi-4, and Llama models. Fine-tuning customizes a model's behavior using your own training "
            "data, which is useful when prompt engineering alone isn't enough. The fine-tuning workflow: "
            "1) Prepare training data in JSONL format with example conversations, 2) Upload to AI Foundry, "
            "3) Configure training parameters (epochs, learning rate, batch size), 4) Monitor training "
            "metrics, 5) Evaluate the fine-tuned model against your test set, 6) Deploy. Best practice: "
            "start with prompt engineering and RAG before considering fine-tuning. Fine-tune only when "
            "you need to change the model's style, format, or domain-specific behavior consistently."
        ),
    },
    {
        "title": "Choosing Between AI Foundry Services",
        "content": (
            "Azure AI has multiple services that complement each other. Azure AI Foundry is the unified "
            "platform for building custom AI applications. Azure OpenAI provides model hosting. Azure AI "
            "Search provides retrieval for RAG. Azure AI Content Safety provides content filtering. "
            "Microsoft Copilot Studio is for building low-code/no-code copilots and chatbots. When to "
            "use what: Use Copilot Studio for simple Q&A bots and business process automation. Use AI "
            "Foundry when you need custom models, complex workflows, or fine-grained control. Use AI "
            "Search when your application needs to retrieve from large document collections. Use Content "
            "Safety when you need to filter user inputs or model outputs in any application."
        ),
    },
    {
        "title": "Azure AI Foundry Networking and Security",
        "content": (
            "Azure AI Foundry supports enterprise networking configurations including virtual network "
            "integration, private endpoints, and managed identity authentication. Hubs can be configured "
            "with a managed virtual network that automatically creates private endpoints to dependent "
            "resources (storage, key vault, search). Data never leaves your network boundary. "
            "Authentication uses Azure RBAC with built-in roles: AI Developer (full project access), "
            "AI Inference Deployment Operator (deploy models only), and Reader. Best practice: use "
            "managed identity instead of API keys, enable managed virtual network for production "
            "workloads, and use Azure Policy to enforce governance across all AI Foundry resources."
        ),
    },
    {
        "title": "Prompt Engineering Best Practices",
        "content": (
            "Effective prompt engineering is critical for AI application quality. Key techniques: "
            "1) System prompts: set the model's role, constraints, and output format clearly. "
            "2) Few-shot examples: include 2-3 examples of desired input-output pairs. "
            "3) Chain-of-thought: ask the model to reason step by step for complex tasks. "
            "4) Output formatting: specify exact JSON schemas or templates for structured output. "
            "5) Grounding instructions: tell the model to only use provided context and say 'I don't know' "
            "when information isn't available. In Azure AI Foundry, use Prompt Flow variants to A/B test "
            "different prompt strategies, and use the evaluation framework to measure which works best. "
            "Remember: prompt engineering is iterative. Test, measure, and refine continuously."
        ),
    },
]

# Override from external JSON file if configured
if KNOWLEDGE_BASE_FILE and os.path.isfile(KNOWLEDGE_BASE_FILE):
    with open(KNOWLEDGE_BASE_FILE, "r", encoding="utf-8") as _f:
        loaded = json.load(_f)
    # Accept either a flat list (preferred) or { "entries": [...] }.
    if isinstance(loaded, dict) and isinstance(loaded.get("entries"), list):
        loaded = loaded["entries"]
    if isinstance(loaded, list):
        DOCUMENTS = [
            d for d in loaded
            if isinstance(d, dict) and "title" in d and "content" in d
        ]


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _build_idf(docs: list[dict]) -> dict[str, float]:
    n = len(docs)
    df: Counter = Counter()
    for doc in docs:
        tokens = set(_tokenize(doc["title"] + " " + doc["content"]))
        for t in tokens:
            df[t] += 1
    return {t: math.log((n + 1) / (freq + 1)) + 1 for t, freq in df.items()}


_IDF = _build_idf(DOCUMENTS)


def search(query: str, top_k: int = 3) -> list[dict]:
    """Simple TF-IDF search over the knowledge base."""
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    query_tf = Counter(query_tokens)

    scored = []
    for doc in DOCUMENTS:
        doc_text = doc["title"] + " " + doc["content"]
        doc_tokens = _tokenize(doc_text)
        doc_tf = Counter(doc_tokens)
        doc_len = len(doc_tokens) or 1

        score = 0.0
        for qt in query_tf:
            tf = doc_tf.get(qt, 0) / doc_len
            idf = _IDF.get(qt, 1.0)
            score += query_tf[qt] * tf * idf

        if score > 0:
            scored.append(
                {
                    "title": doc["title"],
                    "content": doc["content"],
                    "score": round(score, 4),
                }
            )

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]
