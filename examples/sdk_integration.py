"""Build the embeddable SDK from real company infrastructure.

Import build_sdk() from a trusted backend after loading secrets from its secret
manager. The host must authenticate and authorize users before constructing
AuthenticatedActor from its server-side session.
"""

import os
from datetime import timedelta
from pathlib import Path

from support_agent import AgentSDK
from support_agent.handoff import HandoffQueue
from support_agent.knowledge import KnowledgeBase
from support_agent.document_answer import GroqDocumentAnswerer
from support_agent.supply import SupplyStore
from support_agent.supply_http import HttpSupplyStore
from support_agent.supply_llm import configured_chain


def build_sdk() -> AgentSDK:
    api_url = os.getenv("SUPPLY_API_URL", "")
    database = os.getenv("SUPPLY_DB_PATH", "")
    if bool(api_url) == bool(database):
        raise ValueError("Configure exactly one real supply source")
    age = timedelta(minutes=int(os.getenv("SUPPLY_MAX_SYNC_AGE_MINUTES", "30")))
    if api_url:
        source = HttpSupplyStore(api_url, os.environ["SUPPLY_API_TOKEN"], max_sync_age=age)
    else:
        path = Path(database)
        if not path.is_absolute():
            raise ValueError("SUPPLY_DB_PATH must be absolute")
        source = SupplyStore(path, require_live=True, max_sync_age=age)
    handoff_path = Path(os.environ["HANDOFF_DB_PATH"])
    if not handoff_path.is_absolute():
        raise ValueError("HANDOFF_DB_PATH must be absolute")
    handoffs = HandoffQueue(handoff_path)
    knowledge = answerer = None
    qdrant_url = os.getenv("QDRANT_URL", "")
    if qdrant_url:
        if not qdrant_url.startswith("https://") or os.getenv("AI_MODE") != "groq":
            raise ValueError("Document answers require HTTPS Qdrant and AI_MODE=groq")
        collection = os.getenv("QDRANT_COLLECTION", "supply_chain_live_knowledge_v1")
        if not collection.startswith("supply_chain_live_"):
            raise ValueError("Use a live supply-chain Qdrant collection")
        knowledge = KnowledgeBase(url=qdrant_url,
                                  api_key=os.getenv("QDRANT_API_KEY") or None,
                                  collection=collection)
        answerer = GroqDocumentAnswerer(os.environ["GROQ_API_KEY"],
                                       os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
                                       domain="supply_chain")
    mode = os.getenv("SUPPLY_AGENT_MODE", "deterministic")
    if mode == "deterministic":
        sdk = AgentSDK.for_supply_chain(source, handoffs, knowledge=knowledge,
                                        document_answerer=answerer)
    elif mode == "multi":
        providers = configured_chain(
            os.environ["GROQ_API_KEY"], os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
            os.environ["OPENROUTER_API_KEY"],
            os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-120b"))
        sdk = AgentSDK.for_supply_chain(source, handoffs, knowledge=knowledge,
                                        document_answerer=answerer, providers=providers)
    else:
        raise ValueError("SUPPLY_AGENT_MODE must be deterministic or multi")
    if not sdk.ready() or (knowledge and not knowledge.ready()):
        raise RuntimeError("Supply source, handoff sink, or knowledge store is unavailable")
    return sdk
