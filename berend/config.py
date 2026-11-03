"""All settings come from environment variables (or a .env file)."""
import os

from dotenv import load_dotenv

load_dotenv()


def _list(name: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes", "on")


# --- Identity -------------------------------------------------------------
AGENT_NAME = os.getenv("AGENT_NAME", "Berend")

# --- Ollama ---------------------------------------------------------------
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
CHAT_MODEL = os.getenv("CHAT_MODEL", "qwen2.5:14b")          # must support tool calling
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
NUM_CTX = int(os.getenv("NUM_CTX", "16384"))
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "60m")     # keep model in VRAM between questions
OLLAMA_THINK = os.getenv("OLLAMA_THINK", "")                  # "false" disables qwen3-style thinking

# --- Vector store ---------------------------------------------------------
CHROMA_DIR = os.getenv("CHROMA_DIR", "./data/chroma")
COLLECTION = os.getenv("COLLECTION", "knowledge")
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1200"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "200"))
TOP_K = int(os.getenv("TOP_K", "6"))
EMBED_BATCH = int(os.getenv("EMBED_BATCH", "64"))
INGEST_WORKERS = int(os.getenv("INGEST_WORKERS", "8"))        # parallel downloads
EMBED_WORKERS = int(os.getenv("EMBED_WORKERS", "2"))          # match OLLAMA_NUM_PARALLEL

# --- Sources to index -----------------------------------------------------
LOCAL_DATA_DIRS = _list("LOCAL_DATA_DIRS")                    # /data/docs,/data/runbooks
LOCAL_EXTENSIONS = _list("LOCAL_EXTENSIONS") or [
    ".md", ".txt", ".rst", ".adoc", ".html", ".htm", ".pdf", ".yaml", ".yml",
]

DOCS_SITE_URLS = _list("DOCS_SITE_URLS")                      # https://docs.example.nl/
DOCS_MAX_PAGES = int(os.getenv("DOCS_MAX_PAGES", "500"))

GITLAB_URL = os.getenv("GITLAB_URL", "")
GITLAB_TOKEN = os.getenv("GITLAB_TOKEN", "")                  # read_api scope is enough
GITLAB_PROJECTS = _list("GITLAB_PROJECTS")                    # group/project,group/other
GITLAB_EXTENSIONS = _list("GITLAB_EXTENSIONS") or [".md", ".rst", ".adoc", ".txt"]
GITLAB_INDEX_WIKIS = _bool("GITLAB_INDEX_WIKIS", True)

# --- Web search (live, at question time) ----------------------------------
# searxng (self-hosted, recommended) | brave | google | none
WEB_SEARCH_PROVIDER = os.getenv("WEB_SEARCH_PROVIDER", "none").lower()
SEARXNG_URL = os.getenv("SEARXNG_URL", "")                    # http://searxng:8080
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")              # legacy CSE, ends 2027-01-01
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "")
FETCH_ALLOWED_DOMAINS = _list("FETCH_ALLOWED_DOMAINS")        # empty = any domain

# --- Slack ----------------------------------------------------------------
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")            # xoxb-...
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN", "")            # xapp-... (Socket Mode)
SLACK_ALLOWED_CHANNELS = _list("SLACK_ALLOWED_CHANNELS")      # empty = all channels
SLACK_THREAD_FOLLOWUPS = _bool("SLACK_THREAD_FOLLOWUPS", True)  # answer in-thread without @mention

STREAM_UPDATE_SECONDS = float(os.getenv("STREAM_UPDATE_SECONDS", "1.2"))  # Slack edit rate
FEEDBACK_LOG = os.getenv("FEEDBACK_LOG", "./data/feedback.jsonl")

# --- ArgoCD monitor -------------------------------------------------------
ARGOCD_URL = os.getenv("ARGOCD_URL", "").rstrip("/")           # https://argocd.apps.cluster.example.nl
ARGOCD_TOKEN = os.getenv("ARGOCD_TOKEN", "")                   # read-only account token
ARGOCD_INSECURE = _bool("ARGOCD_INSECURE", False)
ARGOCD_PROJECTS = _list("ARGOCD_PROJECTS")                     # empty = all projects
ARGOCD_SELECTOR = os.getenv("ARGOCD_SELECTOR", "")             # label selector, e.g. env=prod
ARGOCD_POLL_SECONDS = int(os.getenv("ARGOCD_POLL_SECONDS", "60"))
ARGOCD_CONFIRM_POLLS = int(os.getenv("ARGOCD_CONFIRM_POLLS", "2"))     # debounce flapping
ARGOCD_ALERT_OUT_OF_SYNC = _bool("ARGOCD_ALERT_OUT_OF_SYNC", True)
ARGOCD_PROGRESSING_MINUTES = int(os.getenv("ARGOCD_PROGRESSING_MINUTES", "15"))
ARGOCD_STATE_FILE = os.getenv("ARGOCD_STATE_FILE", "./data/argocd_state.json")
ARGOCD_STORM_THRESHOLD = int(os.getenv("ARGOCD_STORM_THRESHOLD", "5"))  # >= N new problems: 1 grouped alert
ARGOCD_IGNORE_APPS = _list("ARGOCD_IGNORE_APPS")               # regexes on app name
ARGOCD_MUTE_ANNOTATION = os.getenv("ARGOCD_MUTE_ANNOTATION", "berend.io/mute")
ARGOCD_FETCH_LOGS = _bool("ARGOCD_FETCH_LOGS", False)          # needs `logs, get` RBAC; logs reach the LLM
ARGOCD_LOG_LINES = int(os.getenv("ARGOCD_LOG_LINES", "30"))
ALERT_AI_ANALYSIS = _bool("ALERT_AI_ANALYSIS", True)
ALERT_LANGUAGE = os.getenv("ALERT_LANGUAGE", "English")
ALERT_REMIND_HOURS = float(os.getenv("ALERT_REMIND_HOURS", "4"))   # 0 = no reminders

# --- Mattermost (alerts channel) ------------------------------------------
# Either bot token + channel id (threads, resolves update the original post) or an incoming webhook.
MATTERMOST_URL = os.getenv("MATTERMOST_URL", "").rstrip("/")
MATTERMOST_TOKEN = os.getenv("MATTERMOST_TOKEN", "")
MATTERMOST_CHANNEL_ID = os.getenv("MATTERMOST_CHANNEL_ID", "")
MATTERMOST_WEBHOOK_URL = os.getenv("MATTERMOST_WEBHOOK_URL", "")
MATTERMOST_USERNAME = os.getenv("MATTERMOST_USERNAME", AGENT_NAME)
MATTERMOST_ICON_URL = os.getenv("MATTERMOST_ICON_URL", "")
MATTERMOST_CHAT = _bool("MATTERMOST_CHAT", True)               # answer @mentions/DMs (bot token needed)
MATTERMOST_CHAT_CHANNELS = _list("MATTERMOST_CHAT_CHANNELS")   # empty = any channel the bot is in
MATTERMOST_THREAD_FOLLOWUPS = _bool("MATTERMOST_THREAD_FOLLOWUPS", True)

SLACK_ENABLED = bool(SLACK_BOT_TOKEN and SLACK_APP_TOKEN)
MATTERMOST_ENABLED = bool((MATTERMOST_URL and MATTERMOST_TOKEN and MATTERMOST_CHANNEL_ID)
                          or MATTERMOST_WEBHOOK_URL)
ARGOCD_ENABLED = bool(ARGOCD_URL and ARGOCD_TOKEN)
MONITOR_ENABLED = ARGOCD_ENABLED and MATTERMOST_ENABLED

# --- Agent ----------------------------------------------------------------
MAX_TOOL_ROUNDS = int(os.getenv("MAX_TOOL_ROUNDS", "5"))
HISTORY_MESSAGES = int(os.getenv("HISTORY_MESSAGES", "10"))
REINDEX_INTERVAL_HOURS = float(os.getenv("REINDEX_INTERVAL_HOURS", "0"))  # 0 = off
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))
HEALTH_PORT = int(os.getenv("HEALTH_PORT", "8080"))           # /healthz /readyz /metrics; 0 = off
USER_AGENT = os.getenv("USER_AGENT", "berend/1.0")
# Internal CA? Set REQUESTS_CA_BUNDLE=/path/ca.pem - requests picks it up automatically.
