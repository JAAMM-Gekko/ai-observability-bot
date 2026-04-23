# backend/agent.py

import os
import re
import time
from dotenv import load_dotenv
import chromadb
import openai
from beeai_framework.backend.chat import ChatModel
from beeai_framework.emitter.emitter import Emitter
from beeai_framework.tools.tool import Tool
from beeai_framework.workflows.agent import AgentWorkflow, AgentWorkflowInput
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer

try:
    import openlit
except ImportError:
    openlit = None

try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.resources import Resource
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False

# Disable tokenizer parallelism to avoid warnings and potential conflicts
os.environ["TOKENIZERS_PARALLELISM"] = "false"

load_dotenv()

# Configuration - REPLACE THESE VALUES
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
# OTEL: use env so Docker can point to host (e.g. host.docker.internal:4328) or Splunk VM (10.0.0.249:4328)
OTEL_ENDPOINT = os.getenv("OTEL_ENDPOINT", "http://localhost:4328").rstrip("/")
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "openai-sidecar-test")
ENVIRONMENT = os.getenv("OTEL_ENVIRONMENT", "sidecar-agent")

tracer = None
if OTEL_AVAILABLE:
    try:
        tracer = trace.get_tracer(SERVICE_NAME)
    except Exception:
        tracer = None

# === Initialize OpenLIT ===
all_evals = None
all_guards = None
if openlit:
    try:
        openlit.init(
            otlp_endpoint=OTEL_ENDPOINT,
            disable_metrics=False,
            environment=ENVIRONMENT,
        )
        all_evals = openlit.evals.All(collect_metrics=True)
        all_guards = openlit.guard.All(
            provider="openai",
            api_key=OPENAI_API_KEY,
            collect_metrics=True
        )
        print("OpenLIT initialized successfully")
    except Exception as e:
        print(f"OpenLIT init failed: {e}")

# === Monkeypatch Global OpenAI SDK ===
original_create = openai.chat.completions.create

def patched_create(*args, **kwargs):
    try:
        messages = kwargs.get("messages", [])
        prompt = messages[-1]['content'] if messages and messages[-1]['role'] == 'user' else "<no_user_prompt>"

        response = original_create(*args, **kwargs)
        text = response.choices[0].message.content if response.choices else "<empty_response>"

        # === Evaluation ===
        results = None
        if all_evals:
            try:
                results = all_evals.measure(prompt=prompt, text=text, contexts=[])
                print("test2: evaluation done")
            except Exception as e:
                print(f"Evaluation failed: {e}")

        # Use current global TracerProvider (set at startup by setup_splunk_otel).
        # Module-level tracer was created at import time with no-op provider, so spans
        # would never export; get tracer at request time so Splunk receives llm.prompt/llm.response.
        if OTEL_AVAILABLE:
            try:
                current_tracer = trace.get_tracer(SERVICE_NAME)
                with current_tracer.start_as_current_span("openai_sidecar_intercept") as span:
                    span.set_attribute("service.name", SERVICE_NAME)
                    span.set_attribute("llm.prompt", prompt)
                    span.set_attribute("llm.response", text)

                    if results and hasattr(results, 'verdict') and results.verdict != "no":
                        span.set_attribute("eval.verdict", results.verdict)
                        span.set_attribute("eval.evaluation", results.evaluation or "")
                        span.set_attribute("eval.score", results.score or 0)
                        span.set_attribute("eval.classification", results.classification or "")
                        span.set_attribute("eval.explanation", results.explanation or "")
                        print("Evaluation Results:", results)

                    elif all_guards:
                        print("Running guardrail checks...")
                        try:
                            guard_results = all_guards.detect(text=prompt)
                            print("Guardrail Results:", guard_results or "None")

                            if guard_results and guard_results.verdict != "none":
                                span.set_attribute("guard.verdict", guard_results.verdict)
                                span.set_attribute("guard.score", guard_results.score or 0)
                                span.set_attribute("guard.guard", guard_results.guard or "")
                                span.set_attribute("guard.classification", guard_results.classification or "")
                                span.set_attribute("guard.explanation", guard_results.explanation or "")
                        except Exception as e:
                            print(f"Guardrail check failed: {e}")

                    trace.get_tracer_provider().force_flush(timeout_millis=2000)
                    print("test4: span flushed")
            except Exception as e:
                print(f"Tracing failed: {e}")
        return response

    except Exception as e:
        print(f"Sidecar Error: {e}")
        return original_create(*args, **kwargs)

openai.chat.completions.create = patched_create
print("OpenAI SDK patched successfully")
print("Sidecar is now listening...")

# Configuration constants for the RAG system
EMBEDDING_MODEL_NAME = 'all-MiniLM-L6-v2'
CHROMA_PERSIST_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "my_chroma_db")
CHROMA_COLLECTION_NAME = "company_faqs"
FAQ_NO_RESULTS_SENTINEL = "No relevant information found in the FAQs."

# Runtime policy toggles
RETRIEVAL_FALLBACK_ENABLED = os.getenv("RETRIEVAL_FALLBACK_ENABLED", "1") == "1"
RETRIEVAL_MIN_CONTEXT_CHARS = int(os.getenv("RETRIEVAL_MIN_CONTEXT_CHARS", "80"))
MAX_POLICY_REWRITES = int(os.getenv("MAX_POLICY_REWRITES", "2"))
S6_MODEL = os.getenv("S6_MODEL", "gpt-4o")
NONS6_MODEL = os.getenv("NONS6_MODEL", "gpt-4o-mini")

# === Skill path ===
# backend/agent.py is in backend/, so skills/ is sibling of backend/
SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skills")
CANNABIS_MEDICAL_SKILL_PATH = os.path.join(SKILLS_DIR, "cannabis-medical-safety", "SKILL.md")
CONSTRAINTS_PATH = os.path.join(os.path.dirname(__file__), "constraints.md")

# Global variables
_embedding_model = None
_chroma_collection = None
_llm = None
_faq_tool_instance = None

_agent_workflow = None                 # single workflow for both retail FAQ and medical safety paths
_medical_agent_workflow = None

_tracer_provider = None
_tracer = None

# Cached skill instructions (loaded once)
_medical_skill_instructions = None
_constraints_config = None

DEFAULT_S6_SUBSTITUTIONS = {
    "sleep": "couch potato",
    "insomnia": "deep evening rest",
    "pain": "relaxation",
    "analgesic": "physical comfort",
    "anxiety": "peace of mind",
}
DEFAULT_S6_FORBIDDEN_TERMS = [
    "s6",
    "dosage",
    "mg",
    "milligram",
    "prescription",
    "diagnose",
]


def _load_skill_instructions(skill_path: str) -> str:
    """Load a SKILL.md file (manifest/instructions) from disk."""
    try:
        with open(skill_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        # Fail safe: if skill missing, return a minimal safe policy.
        # (So production doesn't crash if the file isn't mounted in Docker.)
        return (
            "---\n"
            "name: cannabis-medical-safety\n"
            "description: Safety fallback skill; file missing.\n"
            "---\n\n"
            "# Cannabis Medical Information Skill (Fallback)\n"
            "If a user asks for medical advice, respond with a disclaimer and advise consulting a licensed clinician.\n"
            "Do not provide dosage numbers or medical claims.\n"
        )
    except Exception as e:
        return (
            "---\n"
            "name: cannabis-medical-safety\n"
            "description: Safety fallback skill; file unreadable.\n"
            "---\n\n"
            f"# Error loading skill: {type(e).__name__}\n"
            "Respond with a disclaimer and recommend consulting a licensed clinician.\n"
            "Do not provide dosage numbers or medical claims.\n"
        )


def _load_constraints_config(constraints_path: str) -> dict:
    """Load substitution and forbidden-term rules from constraints.md."""
    config = {
        "substitutions": dict(DEFAULT_S6_SUBSTITUTIONS),
        "forbidden_terms": list(DEFAULT_S6_FORBIDDEN_TERMS),
    }
    try:
        if not os.path.exists(constraints_path):
            return config
        with open(constraints_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.lower().startswith("forbidden:"):
                    term = line.split(":", 1)[1].strip().lower()
                    if term and term not in config["forbidden_terms"]:
                        config["forbidden_terms"].append(term)
                    continue
                if "=>" in line:
                    left, right = line.split("=>", 1)
                    src = left.strip().lower()
                    dst = right.strip()
                    if src and dst:
                        config["substitutions"][src] = dst
    except Exception as e:
        print(f"[Constraints] Failed to load constraints config: {e}")
    return config


def _apply_s6_substitutions(text: str) -> tuple[str, int]:
    """Apply S6 vocabulary substitutions and return replaced count."""
    if not text:
        return text, 0
    cfg = _constraints_config or {
        "substitutions": DEFAULT_S6_SUBSTITUTIONS,
        "forbidden_terms": DEFAULT_S6_FORBIDDEN_TERMS,
    }
    out = text
    replaced = 0
    for src, dst in cfg["substitutions"].items():
        pattern = re.compile(rf"\b{re.escape(src)}\b", re.IGNORECASE)
        out, n = pattern.subn(dst, out)
        replaced += n
    return out, replaced


def _detect_forbidden_s6_terms(text: str) -> list[str]:
    cfg = _constraints_config or {
        "substitutions": DEFAULT_S6_SUBSTITUTIONS,
        "forbidden_terms": DEFAULT_S6_FORBIDDEN_TERMS,
    }
    normalized = _normalize(text)
    found: list[str] = []
    for term in cfg["forbidden_terms"]:
        if term and term in normalized:
            found.append(term)
    return found


def _normalize(text: str) -> str:
    """Make matching easier (hyphens/spaces/etc.)."""
    t = (text or "").lower()
    t = t.replace("-", " ")               # self-harm -> self harm
    t = re.sub(r"\s+", " ", t).strip()    # collapse extra spaces
    return t

ESCALATION_TERMS = [
    # Pregnancy / breastfeeding
    "pregnant", "pregnancy", "expecting", "trimester", "preggo",
    "breastfeeding", "breast feeding", "nursing", "lactating",

    # Chest pain / heart danger
    "chest pain", "heart attack", "tightness in chest", "pressure in chest",
    "palpitations", "heart racing",

    # Seizures
    "seizure", "seizures", "convulsion", "convulsions",

    # Self-harm / suicide
    "suicidal", "self harm", "harm myself", "kill myself", "want to die", "end my life",

    # Pediatric
    "pediatric", "child", "kid", "teen", "minor", "under 18", "my son", "my daughter",
]

def _is_medical_skill_query(user_query: str) -> bool:
    """
    Router: if query requires clinical guardrails (dosing, drug interactions,
    escalation emergencies), route to medical safety skill workflow.

    General wellness/lifestyle queries (sleep, anxiety, relaxation) flow straight
    to LLM to receive non-medical descriptive responses per sponsor guidelines.
    """
    q = _normalize(user_query)

    # Gate 1 — hard escalation terms (defined externally, non-negotiable)
    if any(term in q for term in ESCALATION_TERMS):
        return True

    # Gate 2 — strictly clinical/dosing terms only
    hard_clinical = [
        "dose", "dosage", "mg", "milligram", "titrate",
        "interaction", "contraindication", "cyp", "cyp450",
        "overdose", "withdrawal", "seizure", "epilepsy",
        "blood pressure", "hypertension", "ssri", "warfarin",
        "blood thinner", "pharmacist", "prescription",
    ]
    if any(t in q for t in hard_clinical):
        return True

    # Gate 3 — "strain for [health topic]" compound rule (narrow, intentional)
    health_topics = [
        "sleep", "insomnia", "anxiety", "panic", "pain",
        "inflammation", "nausea", "ptsd", "depression",
        "arthritis", "cancer", "migraine", "adhd",
    ]
    if "strain" in q and any(t in q for t in health_topics):
        return True

    # Gate 4 — clinical sleep queries only (sponsor flagged "sleep" as missing)
    # casual sleep queries ("help me sleep", "good for sleep") are intentionally
    # excluded and will receive non-medical lifestyle responses
    sleep_clinical = [
        "sleep aid", "sleeping pill", "melatonin dose", "how much melatonin"
    ]
    if any(t in q for t in sleep_clinical):
        return True

    return False


ROUTER_MODEL = os.getenv("INTENT_ROUTER_MODEL", "gpt-4o-mini")


def _select_route_model(is_s6: bool) -> str:
    return S6_MODEL if is_s6 else NONS6_MODEL


async def _classify_intent(user_query: str, routing_context: str = "") -> bool:
    """Two-stage router. Returns True = medical, False = retail.

    Stage 1: keyword hard-rules (no LLM, zero cost).
    Stage 2: gpt-4o-mini classifier (temp=0, output: retail | medical).
    """
    if _is_medical_skill_query(user_query):
        return True

    ctx_block = (
        f"\n\nConversation context:\n{routing_context}" if routing_context else ""
    )
    prompt = (
        "Classify the following customer message as either 'retail' or 'medical'.\n"
        "- retail: product info, prices, store hours, strains, flavours, "
        "general cannabis questions\n"
        "- medical: dosage, drug interactions, health conditions, "
        "prescriptions, safety\n"
        f"{ctx_block}\n\n"
        f"Message: {user_query}\n\n"
        "Reply with exactly one word: retail or medical"
    )
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        resp = await client.chat.completions.create(
            model=ROUTER_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=5,
        )
        label = resp.choices[0].message.content.strip().lower()
        return label == "medical"
    except Exception as e:
        print(f"[Router] LLM classify failed, falling back to keyword: {e}")
        return False


def setup_splunk_otel():
    if not OTEL_AVAILABLE:
        print("OpenTelemetry not available - skipping OTEL setup")
        return None

    try:
        base = os.getenv("OTEL_ENDPOINT", "http://localhost:4328").rstrip("/")
        otel_traces_url = base if base.endswith("/v1/traces") else f"{base}/v1/traces"
        svc_name = os.getenv("OTEL_SERVICE_NAME", "beeai-faq-agent")
        env_name = os.getenv("OTEL_ENVIRONMENT", "production")

        print("Setting up Splunk SignalFX OTEL integration...")
        print(f"   Endpoint: {otel_traces_url}")
        print(f"   Service: {svc_name}")
        print(f"   Environment: {env_name}")

        resource = Resource.create({
            "service.name": svc_name,
            "service.version": "1.0.0",
            "deployment.environment": env_name,
            "telemetry.sdk.name": "beeai-framework",
            "telemetry.sdk.version": "0.1.17"
        })

        tracer_provider = TracerProvider(resource=resource)

        otlp_exporter = OTLPSpanExporter(
            endpoint=otel_traces_url,
            headers={}
        )

        tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
        trace.set_tracer_provider(tracer_provider)

        print("Splunk SignalFX OTEL integration configured successfully")
        return tracer_provider

    except Exception as e:
        print(f"Failed to configure Splunk SignalFX OTEL: {e}")
        print("Check your OTEL endpoint and configuration")
        return None


def test_span_export(tracer_provider, endpoint: str):
    try:
        print(f"Testing span export to {endpoint}...")

        test_tracer = trace.get_tracer("span-test")
        with test_tracer.start_as_current_span("test_span") as span:
            span.set_attribute("test.attribute", "span_export_test")
            span.set_attribute("test.timestamp", time.time())
            span.set_attribute("test.service", "beeai-faq-agent")

        tracer_provider.force_flush(timeout_millis=3000)

        print("Span export test completed successfully")
        print("Check your Splunk dashboard for the test span")
        return True

    except Exception as e:
        print(f"Span export test failed: {e}")
        print("Check your OTEL endpoint connectivity")
        return False


def _setup_rag_system():
    global _embedding_model, _chroma_collection, _llm, _faq_tool_instance
    global _agent_workflow, _medical_agent_workflow
    global _tracer_provider, _tracer
    global _medical_skill_instructions, _constraints_config

    if (_embedding_model and _chroma_collection and _llm and _faq_tool_instance and
            _agent_workflow and _medical_agent_workflow and
            _medical_skill_instructions and _constraints_config):
        print("RAG system already set up.")
        return True

    print("Setting up RAG system components...")

    if OTEL_AVAILABLE:
        try:
            _tracer_provider = setup_splunk_otel()
            if _tracer_provider:
                _tracer = trace.get_tracer("beeai-faq-agent")
                global tracer
                tracer = trace.get_tracer(SERVICE_NAME)
                print("OpenTelemetry tracer initialized for observability")

                if os.getenv("OTEL_SKIP_SPAN_TEST", "1") != "0":
                    print("Skipping span export test (set OTEL_SKIP_SPAN_TEST=0 to enable)")
                else:
                    otel_endpoint = os.getenv("OTEL_ENDPOINT", "http://localhost:4328")
                    span_test_success = test_span_export(_tracer_provider, otel_endpoint)
                    if not span_test_success:
                        print("Warning: Span export test failed - traces may not be reaching Splunk")
                    else:
                        print("Span export test passed - traces are being sent to Splunk successfully")
            else:
                print("OpenTelemetry setup failed - continuing without observability")
        except Exception as e:
            print(f"Error setting up OpenTelemetry: {e}")
            print("Continuing without observability")
    else:
        print("OpenTelemetry not available - running without observability")

    # Load skill text once (keeps it out of base prompt unless used)
    _medical_skill_instructions = _load_skill_instructions(CANNABIS_MEDICAL_SKILL_PATH)
    if os.path.exists(CANNABIS_MEDICAL_SKILL_PATH):
        print(f"Loaded medical skill from {CANNABIS_MEDICAL_SKILL_PATH}")
    else:
        print(f"Medical skill file not found at {CANNABIS_MEDICAL_SKILL_PATH} - using fallback skill text")

    _constraints_config = _load_constraints_config(CONSTRAINTS_PATH)
    print(
        "[Constraints] loaded substitutions="
        f"{len(_constraints_config.get('substitutions', {}))} "
        f"forbidden_terms={len(_constraints_config.get('forbidden_terms', []))}"
    )

    try:
        _embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
        print("Embedding model loaded.")
    except Exception as e:
        print(f"Error loading embedding model: {e}")
        return False

    try:
        chroma_client = chromadb.PersistentClient(path=CHROMA_PERSIST_PATH)
        _chroma_collection = chroma_client.get_or_create_collection(name=CHROMA_COLLECTION_NAME)
        print(f"ChromaDB collection '{CHROMA_COLLECTION_NAME}' ready with {_chroma_collection.count()} documents.")
        print("CHROMA_PERSIST_PATH =", CHROMA_PERSIST_PATH)
        print("CHROMA_COLLECTION_NAME =", CHROMA_COLLECTION_NAME)
        print("CHROMA_DOC_COUNT =", _chroma_collection.count())
    except Exception as e:
        print(f"Error connecting to ChromaDB: {e}")
        return False

    try:
        _llm = ChatModel.from_name(os.environ.get("OPENAI_MODEL", "openai:gpt-4o"))
        print("OpenAI LLM initialized.")
    except Exception as e:
        print(f"Error initializing LLM: {e}")
        return False

    _faq_tool_instance = FAQTool(embedding_model=_embedding_model, chroma_collection=_chroma_collection)
    print("FAQTool instance created.")

    # --- Retail/FAQ workflow ---
    _agent_workflow = AgentWorkflow(name='Company FAQ Assistant')
    _agent_workflow.add_agent(
        name='FAQAgent',
        role='A fun, friendly Washington cannabis budtender focused on compliance-safe FAQ guidance.',
        instructions=(
            "You are a Washington State cannabis retail compliance assistant with a fun budtender vibe. "
            "Your primary goal is to answer using only the provided FAQ context. "
            "If no relevant FAQ context is provided, say you cannot find it in the store FAQs. "

            "Do NOT provide medical advice, dosing advice, or therapeutic health claims. "
            "Never state that cannabis treats, cures, prevents, or alleviates medical conditions. "

            "Allowed content includes flavor/aroma descriptions, factual cannabinoid and terpene information, "
            "potency details, consumption methods, and neutral consumer-reported experiences "
            "(example: customers often describe this as uplifting or relaxing). "

            "If a user asks a health or medical question, those questions are handled by a separate "
            "medical safety skill, so do not answer them here. "

            "Do NOT try to use the 'faq_lookup_tool' on your own if context is already provided. "

            "You may receive a conversation summary and/or recent chat history as part of the prompt. "
            "Use this context to understand follow-up questions (e.g. 'tell me more', 'what about the other one') "
            "and maintain conversational coherence across multiple turns."
        ),
        tools=[_faq_tool_instance],
        llm=_llm,
    )

    # --- Medical safety workflow (skill-based; only invoked on medical-ish queries) ---
    _medical_agent_workflow = AgentWorkflow(name="Cannabis Medical Safety")
    _medical_safety_instructions = (
        _medical_skill_instructions + "\n\n"
        "You may receive a conversation summary and/or recent chat history as part of the prompt. "
        "Use this context to understand follow-up questions and maintain conversational coherence, "
        "while still following all safety and disclaimer rules above."
    )
    _medical_agent_workflow.add_agent(
        name="MedicalSafetyAgent",
        role="A cannabis medical information safety specialist.",
        instructions=_medical_safety_instructions,
        tools=[],
        llm=_llm,
    )

    print("Agent workflows created: FAQAgent + MedicalSafetyAgent (skill-based).")
    return True


class FAQTool(Tool):
    name: str = "faq_lookup_tool"
    description: str = (
        "Searches the company's frequently asked questions for relevant answers using semantic search. "
        "Use this tool when the user asks a question about company policies, products, or general FAQs. "
        "Input should be a question string."
    )

    class FAQToolInput(BaseModel):
        query: str = Field(description="The question to lookup in the company FAQs.")

    @property
    def input_schema(self) -> type[BaseModel]:
        return self.FAQToolInput

    def _create_emitter(self) -> Emitter | None:
        return Emitter()

    def __init__(self, embedding_model: SentenceTransformer, chroma_collection: chromadb.Collection):
        super().__init__()
        self.embedding_model = embedding_model
        self.chroma_collection = chroma_collection

    async def _run(self, query: str) -> str:
        if _tracer:
            with _tracer.start_as_current_span("faq_tool_execution") as span:
                span.set_attribute("tool.name", self.name)
                span.set_attribute("tool.description", self.description)
                span.set_attribute("faq.query", query)
                span.set_attribute("faq.query_length", len(query))

                try:
                    with _tracer.start_as_current_span("query_embedding") as embedding_span:
                        embedding_span.set_attribute("embedding.model", "sentence-transformers")
                        embedding_span.set_attribute("embedding.model_name", "all-MiniLM-L6-v2")

                        query_embedding = self.embedding_model.encode(query).tolist()
                        embedding_span.set_attribute("embedding.vector_size", len(query_embedding))
                        embedding_span.set_attribute("embedding.success", True)

                    with _tracer.start_as_current_span("chroma_search") as search_span:
                        search_span.set_attribute("chroma.collection", CHROMA_COLLECTION_NAME)
                        search_span.set_attribute("chroma.n_results", 3)

                        results = self.chroma_collection.query(
                            query_embeddings=[query_embedding],
                            n_results=3,
                            include=['documents', 'metadatas']
                        )

                        search_span.set_attribute(
                            "chroma.results_count",
                            len(results.get('documents', [[]])[0]) if results and results.get('documents') else 0
                        )
                        search_span.set_attribute("chroma.search_success", True)

                    with _tracer.start_as_current_span("result_processing") as process_span:
                        retrieved_contexts = []
                        if results and results.get('documents') and results['documents'][0]:
                            for i in range(len(results['documents'][0])):
                                doc_content = results['documents'][0][i]
                                metadata = results['metadatas'][0][i]
                                question = metadata.get('question', 'N/A')
                                answer = metadata.get('answer', doc_content)
                                retrieved_contexts.append(f"Question: {question}\nAnswer: {answer}")

                        process_span.set_attribute("processing.contexts_count", len(retrieved_contexts))
                        process_span.set_attribute("processing.success", True)

                    if not retrieved_contexts:
                        span.set_attribute("faq.no_results", True)
                        span.set_attribute("faq.status", "no_results")
                        return "No relevant information found in the FAQs."

                    context_string = "\n\n".join(retrieved_contexts)
                    span.set_attribute("faq.results_found", True)
                    span.set_attribute("faq.status", "success")
                    span.set_attribute("faq.response_length", len(context_string))

                    return context_string

                except Exception as e:
                    span.set_attribute("faq.status", "error")
                    span.set_attribute("error.message", str(e))
                    span.set_attribute("error.type", type(e).__name__)

                    print(f"Error in FAQ tool execution: {e}")
                    return f"Error processing query for FAQ lookup: {e}"
        else:
            try:
                query_embedding = self.embedding_model.encode(query).tolist()

                results = self.chroma_collection.query(
                    query_embeddings=[query_embedding],
                    n_results=3,
                    include=['documents', 'metadatas']
                )

                retrieved_contexts = []
                if results and results.get('documents') and results['documents'][0]:
                    for i in range(len(results['documents'][0])):
                        doc_content = results['documents'][0][i]
                        metadata = results['metadatas'][0][i]
                        question = metadata.get('question', 'N/A')
                        answer = metadata.get('answer', doc_content)
                        retrieved_contexts.append(f"Question: {question}\nAnswer: {answer}")

                if not retrieved_contexts:
                    return "No relevant information found in the FAQs."

                context_string = "\n\n".join(retrieved_contexts)
                return context_string

            except Exception as e:
                return f"Error processing query for FAQ lookup: {e}"


def _is_retrieved_info_usable(retrieved_info: str) -> tuple[bool, str]:
    text = (retrieved_info or "").strip()
    if not text:
        return False, "empty"
    if FAQ_NO_RESULTS_SENTINEL.lower() in text.lower():
        return False, "no_results"
    if text.lower().startswith("error processing query for faq lookup"):
        return False, "tool_error"
    if "Question:" not in text or "Answer:" not in text:
        return False, "missing_qa_markers"
    if len(text) < RETRIEVAL_MIN_CONTEXT_CHARS:
        return False, "below_min_context_chars"
    return True, "ok"


def _build_retail_fallback_prompt(
    user_query: str,
    routing_context: str = "",
) -> str:
    context_block = f"\n\nConversation context:\n{routing_context}" if routing_context else ""

    return (
        "You are a Washington cannabis retail compliance assistant.\n"
        "No suitable answer was found in the store FAQ database for this question.\n"
        "Answer using general model knowledge while staying conservative and compliant.\n"
        "If uncertain, acknowledge uncertainty and avoid fabricating store-specific facts.\n"
        "Do NOT provide medical advice, dosage guidance, or therapeutic claims."
        f"{context_block}\n\n"
        f"User Question: {user_query}"
    )


def _check_response_constraints(
    answer: str,
    user_query: str,
    is_s6: bool = False,
) -> tuple[bool, list[str]]:
    violations: list[str] = []
    normalized = _normalize(answer)
    query_norm = _normalize(user_query)
    if any(token in normalized for token in ["cure", "treat", "heals", "therapeutic", "medical benefit"]):
        violations.append("therapeutic_or_curative_claim")
    if any(token in normalized for token in ["dosage", "mg ", "milligram", "take twice daily"]):
        violations.append("medical_or_dosage_advice")
    if "ignore" in query_norm and "rule" in query_norm:
        violations.append("prompt_injection_attempt")
    if is_s6:
        leaked_terms = _detect_forbidden_s6_terms(answer)
        if leaked_terms:
            violations.append("s6_forbidden_terms:" + ",".join(leaked_terms))
    return len(violations) == 0, violations


def _build_constraint_rewrite_prompt(
    user_query: str,
    prior_answer: str,
    violations: list[str],
    routing_context: str,
) -> str:
    context_block = f"\n\nConversation context:\n{routing_context}" if routing_context else ""
    return (
        "Rewrite the answer to satisfy compliance constraints.\n"
        "Keep it concise, useful, and policy-safe.\n"
        f"Violation categories to fix: {', '.join(violations)}\n"
        f"{context_block}\n\n"
        f"User question: {user_query}\n"
        f"Current answer: {prior_answer}\n\n"
        "Return only the revised final answer."
    )


async def _llm_text_completion(
    prompt: str,
    model: str = NONS6_MODEL,
    temperature: float = 0,
    max_tokens: int = 500,
) -> str:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (response.choices[0].message.content or "").strip()


async def validate_or_rewrite_answer(
    user_query: str,
    candidate_answer: str,
    routing_context: str = "",
    is_s6: bool = False,
) -> tuple[str, dict]:
    """Bounded compliance loop to prevent unsafe outputs and infinite rewrites."""
    max_rewrites = max(0, MAX_POLICY_REWRITES)
    current = candidate_answer
    last_violations: list[str] = []

    for attempt in range(max_rewrites + 1):
        if is_s6:
            current, _ = _apply_s6_substitutions(current)
        ok, violations = _check_response_constraints(current, user_query, is_s6=is_s6)
        if ok:
            return current, {
                "passed": True,
                "retries": attempt,
                "violations": [],
                "final_disposition": "compliant",
            }

        last_violations = violations
        if attempt >= max_rewrites:
            break

        rewrite_prompt = _build_constraint_rewrite_prompt(
            user_query=user_query,
            prior_answer=current,
            violations=violations,
            routing_context=routing_context,
        )
        current = await _llm_text_completion(
            prompt=rewrite_prompt,
            model=_select_route_model(is_s6),
            temperature=0,
            max_tokens=500,
        )

    safe_refusal = (
        "I can't provide that specific wording due to compliance requirements. "
        "I can help with neutral, non-medical product information for adults 21+."
    )
    return safe_refusal, {
        "passed": False,
        "retries": max_rewrites,
        "violations": last_violations,
        "final_disposition": "safe_refusal",
    }


async def run_medical_with_constraints(user_query: str, routing_context: str = "") -> str:
    retrieved_info = await _faq_tool_instance._run(user_query)
    retrieval_usable, _ = _is_retrieved_info_usable(retrieved_info)

    med_prompt = _build_medical_prompt(user_query, routing_context)
    if retrieval_usable:
        med_prompt = (
            f"Retrieved Company FAQ Information:\n{retrieved_info}\n\n"
            f"{med_prompt}"
        )
    response = await _medical_agent_workflow.run(
        inputs=[AgentWorkflowInput(prompt=med_prompt)]
    )
    candidate = response.result.final_answer
    final_answer, _ = await validate_or_rewrite_answer(
        user_query=user_query,
        candidate_answer=candidate,
        routing_context=routing_context,
        is_s6=True,
    )
    final_answer, _ = _apply_s6_substitutions(final_answer)
    return final_answer


async def run_retail_with_constraints(
    user_query: str,
    routing_context: str = "",
    is_s6: bool = False,
    trace_span=None,
) -> tuple[str, dict]:
    retrieved_info = await _faq_tool_instance._run(user_query)

    retrieval_usable, retrieval_reason = _is_retrieved_info_usable(retrieved_info)
    fallback_triggered = RETRIEVAL_FALLBACK_ENABLED and (not retrieval_usable)

    if fallback_triggered:
        prompt = _build_retail_fallback_prompt(
            user_query=user_query,
            routing_context=routing_context,
        )
        candidate = await _llm_text_completion(
            prompt=prompt,
            model=_select_route_model(is_s6),
            temperature=0,
            max_tokens=500,
        )
        source = "fallback_llm"
    else:
        prompt = _build_faq_prompt(retrieved_info, user_query, routing_context)
        response = await _agent_workflow.run(
            inputs=[AgentWorkflowInput(prompt=prompt)]
        )
        candidate = response.result.final_answer
        source = "grounded_rag"

    final_answer, compliance_meta = await validate_or_rewrite_answer(
        user_query=user_query,
        candidate_answer=candidate,
        routing_context=routing_context,
        is_s6=is_s6,
    )
    final_answer, substitution_count = _apply_s6_substitutions(final_answer) if is_s6 else (final_answer, 0)
    selected_model = _select_route_model(is_s6)
    meta = {
        "retrieval_hit": retrieval_usable,
        "retrieval_usable": retrieval_usable,
        "retrieval_reason": retrieval_reason,
        "retrieval_fallback_triggered": fallback_triggered,
        "source": source,
        "intent_label": "s6" if is_s6 else "nons6",
        "selected_model": selected_model,
        "substitution_count": substitution_count,
        "compliance_passed": compliance_meta["passed"],
        "compliance_retries": compliance_meta["retries"],
        "compliance_violations": compliance_meta["violations"],
        "final_disposition": (
            "grounded"
            if source == "grounded_rag" and compliance_meta["passed"]
            else "fallback_compliant"
            if source == "fallback_llm" and compliance_meta["passed"]
            else compliance_meta["final_disposition"]
        ),
    }

    if trace_span is not None:
        trace_span.set_attribute("retrieval.hit", bool(meta["retrieval_hit"]))
        trace_span.set_attribute("retrieval.usable", bool(meta["retrieval_usable"]))
        trace_span.set_attribute("retrieval.fallback_triggered", bool(meta["retrieval_fallback_triggered"]))
        trace_span.set_attribute("retrieval.reason", str(meta["retrieval_reason"]))
        trace_span.set_attribute("routing.intent_label", str(meta["intent_label"]))
        trace_span.set_attribute("routing.selected_model", str(meta["selected_model"]))
        trace_span.set_attribute("policy.compliance_passed", bool(meta["compliance_passed"]))
        trace_span.set_attribute("policy.rewrite_count", int(meta["compliance_retries"]))
        trace_span.set_attribute("constraints.substitution_count", int(meta["substitution_count"]))
        trace_span.set_attribute("policy.final_disposition", str(meta["final_disposition"]))

    print(
        "[Orchestrator] "
        f"intent={meta['intent_label']} model={meta['selected_model']} "
        f"source={meta['source']} retrieval_usable={meta['retrieval_usable']} "
        f"fallback={meta['retrieval_fallback_triggered']} "
        f"policy_passed={meta['compliance_passed']} "
        f"rewrites={meta['compliance_retries']} substitutions={meta['substitution_count']} "
        f"disposition={meta['final_disposition']}"
    )
    if meta["compliance_violations"]:
        print(f"[Orchestrator] violations={meta['compliance_violations']}")

    return final_answer, meta


def _build_faq_prompt(retrieved_info: str, user_query: str, routing_context: str = "") -> str:
    """Assemble the final prompt with optional compact routing context."""
    ctx = f"\n\n{routing_context}\n" if routing_context else ""
    return (
        f"Retrieved Company FAQ Information:\n{retrieved_info}"
        f"{ctx}"
        f"\n\nUser Question: {user_query}"
    )


def _build_medical_prompt(user_query: str, routing_context: str = "") -> str:
    """Assemble the medical-skill prompt with optional compact routing context."""
    if routing_context:
        return f"{routing_context}\n\nUser Question: {user_query}"
    return user_query


async def run_faq_agent(
    user_query: str,
    conversation_context: str = "",  # kept for backward compat
    routing_context: str = "",       # compact context: last 2 Qs + one-line summary
) -> str:
    """
    Main entrypoint.
    - Stage 1: keyword hard-rules → medical if matched.
    - Stage 2: gpt-4o-mini classifier (temp=0) with compact routing_context.
    - Medical route: MedicalSafetyAgent (no RAG).
    - Retail route: FAQAgent with RAG.
    """
    if _tracer:
        with _tracer.start_as_current_span("faq_agent_workflow") as span:
            span.set_attribute("workflow.name", "Company FAQ Assistant")
            span.set_attribute("workflow.query", user_query)
            span.set_attribute("workflow.query_length", len(user_query))
            span.set_attribute("workflow.timestamp", time.time())
            span.set_attribute("memory.context_length", len(routing_context))
            span.set_attribute("memory.has_context", bool(routing_context))

            try:
                with _tracer.start_as_current_span("rag_system_setup") as setup_span:
                    if not _setup_rag_system():
                        setup_span.set_attribute("setup.status", "failed")
                        span.set_attribute("workflow.status", "setup_failed")
                        return "Backend RAG system failed to initialize. Please check server logs."
                    setup_span.set_attribute("setup.status", "success")

                # === Intent routing (keyword fast-path + LLM fallback) ===
                use_medical_skill = await _classify_intent(user_query, routing_context)
                span.set_attribute("routing.medical_skill", use_medical_skill)
                span.set_attribute("routing.context_used", bool(routing_context))
                span.set_attribute("routing.intent_label", "s6" if use_medical_skill else "nons6")
                span.set_attribute("routing.selected_model", _select_route_model(use_medical_skill))

                if use_medical_skill:
                    with _tracer.start_as_current_span("medical_skill_execution") as med_span:
                        med_span.set_attribute("agent.name", "MedicalSafetyAgent")
                        final_answer = await run_medical_with_constraints(
                            user_query=user_query,
                            routing_context=routing_context,
                        )
                        med_span.set_attribute("workflow.response_length", len(final_answer))
                        med_span.set_attribute("workflow.success", True)

                        max_len = int(os.getenv("OTEL_TEXT_MAX_LEN", "4096"))
                        if max_len > 0:
                            prompt_attr = user_query if len(user_query) <= max_len else (user_query[:max_len] + "…")
                            answer_attr = final_answer if len(final_answer) <= max_len else (final_answer[:max_len] + "…")
                            span.set_attribute("llm.prompt", prompt_attr)
                            span.set_attribute("llm.response", answer_attr)
                            span.set_attribute("llm.response_length", len(final_answer))

                        span.set_attribute("workflow.status", "success")
                        return final_answer

                # === Retail FAQ path ===
                with _tracer.start_as_current_span("retail_orchestrator_execution") as retail_span:
                    final_answer, retail_meta = await run_retail_with_constraints(
                        user_query=user_query,
                        routing_context=routing_context,
                        is_s6=use_medical_skill,
                        trace_span=retail_span,
                    )
                    retail_span.set_attribute("workflow.response_length", len(final_answer))
                    retail_span.set_attribute("workflow.success", True)
                    span.set_attribute("workflow.status", "success")

                    max_len = int(os.getenv("OTEL_TEXT_MAX_LEN", "4096"))
                    if max_len > 0:
                        prompt_attr = user_query if len(user_query) <= max_len else (user_query[:max_len] + "…")
                        answer_attr = final_answer if len(final_answer) <= max_len else (final_answer[:max_len] + "…")
                        span.set_attribute("llm.prompt", prompt_attr)
                        span.set_attribute("llm.response", answer_attr)
                        span.set_attribute("llm.response_length", len(final_answer))
                    span.set_attribute("policy.final_disposition", retail_meta["final_disposition"])

                    return final_answer

            except Exception as e:
                span.set_attribute("workflow.status", "error")
                span.set_attribute("error.message", str(e))
                span.set_attribute("error.type", type(e).__name__)

                print(f"Error running agent workflow: {e}")
                return f"An error occurred while processing your request: {e}"

    # === No tracer path ===
    if not _setup_rag_system():
        return "Backend RAG system failed to initialize. Please check server logs."

    use_medical_skill = await _classify_intent(user_query, routing_context)
    if use_medical_skill:
        try:
            return await run_medical_with_constraints(
                user_query=user_query,
                routing_context=routing_context,
            )
        except Exception as e:
            print(f"Error running medical skill workflow: {e}")
            return f"An error occurred while processing your request: {e}"

    try:
        final_answer, _ = await run_retail_with_constraints(
            user_query=user_query,
            routing_context=routing_context,
            is_s6=use_medical_skill,
        )
        return final_answer
    except Exception as e:
        print(f"Error running agent workflow: {e}")
        return f"An error occurred while processing your request: {e}"


def get_observability_data() -> dict:
    return {
        "rag_system": {
            "embedding_model_loaded": _embedding_model is not None,
            "chroma_collection_ready": _chroma_collection is not None,
            "llm_initialized": _llm is not None,
            "faq_tool_ready": _faq_tool_instance is not None,
            "agent_workflow_ready": _agent_workflow is not None,
            "medical_skill_loaded": _medical_skill_instructions is not None,
            "medical_skill_path": CANNABIS_MEDICAL_SKILL_PATH,
        },
        "opentelemetry": {
            "available": OTEL_AVAILABLE,
            "tracer_provider_ready": _tracer_provider is not None,
            "tracer_ready": _tracer is not None
        },
        "chroma_db": {
            "collection_name": CHROMA_COLLECTION_NAME,
            "document_count": _chroma_collection.count() if _chroma_collection else 0,
            "persist_path": CHROMA_PERSIST_PATH
        },
        "embedding_model": {
            "name": EMBEDDING_MODEL_NAME,
            "loaded": _embedding_model is not None
        },
        "routing_models": {
            "s6_model": S6_MODEL,
            "nons6_model": NONS6_MODEL,
            "router_model": ROUTER_MODEL,
        },
        "constraints": {
            "loaded": _constraints_config is not None,
            "path": CONSTRAINTS_PATH,
            "substitutions": len((_constraints_config or {}).get("substitutions", {})),
            "forbidden_terms": len((_constraints_config or {}).get("forbidden_terms", [])),
        },
        "status": "ready" if all([
            _embedding_model, _chroma_collection, _llm,
            _faq_tool_instance, _agent_workflow,
            _medical_skill_instructions, _constraints_config
        ]) else "initializing"
    }