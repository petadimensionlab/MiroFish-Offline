"""
Configuration Management
Loads configuration from .env file in project root directory
"""

import os
from dotenv import load_dotenv

# Load .env file from project root
# Path: MiroFish/.env (relative to backend/app/config.py)
project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')

if os.path.exists(project_root_env):
    load_dotenv(project_root_env, override=True)
else:
    # If no .env in root, try to load environment variables (for production)
    load_dotenv(override=True)


class Config:
    """Flask configuration class"""

    # Flask configuration
    SECRET_KEY = os.environ.get('SECRET_KEY', 'mirofish-secret-key')
    DEBUG = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'

    # JSON configuration - disable ASCII escaping to display Chinese directly (not as \uXXXX)
    JSON_AS_ASCII = False

    # LLM configuration (unified OpenAI format)
    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'http://localhost:11434/v1')
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', 'qwen2.5:32b')
    # Larger-context variant used ONLY by stages that need it (ontology / config /
    # report). Bulk stages (NER, profiles, simulation) use LLM_MODEL_NAME (small
    # num_ctx) so they run fast and parallelize. Empty -> same as LLM_MODEL_NAME.
    LLM_MODEL_NAME_LARGE = os.environ.get('LLM_MODEL_NAME_LARGE', '') or LLM_MODEL_NAME

    # Ollama context window per request. Most steps use the small default;
    # ontology generation needs a larger one (it may feed up to 50,000 chars).
    # NOTE: Ollama's OpenAI-compatible endpoint ignores extra_body.options.num_ctx,
    # so the effective per-slot context is the *derived model's* num_ctx.
    OLLAMA_NUM_CTX = int(os.environ.get('OLLAMA_NUM_CTX', '4096'))
    OLLAMA_NUM_CTX_ONTOLOGY = int(os.environ.get('OLLAMA_NUM_CTX_ONTOLOGY', '16384'))

    # Reasoning/"thinking" control for reasoning models (e.g. qwen3.5). Ollama
    # returns thinking in a separate `reasoning` field, and the OpenAI-compatible
    # response's `content` can be empty if the model spends its whole budget
    # thinking. Set to 'none' to force direct answers for JSON pipelines.
    # Empty string = do not send the parameter (non-reasoning models unaffected).
    LLM_REASONING_EFFORT = os.environ.get('LLM_REASONING_EFFORT', '')

    # Neo4j configuration
    NEO4J_URI = os.environ.get('NEO4J_URI', 'bolt://localhost:7687')
    NEO4J_USER = os.environ.get('NEO4J_USER', 'neo4j')
    NEO4J_PASSWORD = os.environ.get('NEO4J_PASSWORD', 'mirofish')

    # Embedding configuration
    EMBEDDING_MODEL = os.environ.get('EMBEDDING_MODEL', 'nomic-embed-text')
    EMBEDDING_BASE_URL = os.environ.get('EMBEDDING_BASE_URL', 'http://localhost:11434')
    # Embedding API style: 'auto' | 'ollama' | 'openai'.
    # 'auto' selects Ollama's native /api/embed for *:11434, otherwise OpenAI /v1/embeddings.
    EMBEDDING_API_STYLE = os.environ.get('EMBEDDING_API_STYLE', 'auto')
    # API key for the OpenAI-compatible embedding endpoint (ignored by Ollama).
    EMBEDDING_API_KEY = os.environ.get('EMBEDDING_API_KEY')

    # File upload configuration
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '../uploads')
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}

    # Text processing configuration
    DEFAULT_CHUNK_SIZE = 500  # Default chunk size
    DEFAULT_CHUNK_OVERLAP = 50  # Default overlap size

    # OASIS simulation configuration
    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '10'))
    OASIS_SIMULATION_DATA_DIR = os.path.join(os.path.dirname(__file__), '../uploads/simulations')

    # OASIS platform available actions configuration
    OASIS_TWITTER_ACTIONS = [
        'CREATE_POST', 'LIKE_POST', 'REPOST', 'FOLLOW', 'DO_NOTHING', 'QUOTE_POST'
    ]
    OASIS_REDDIT_ACTIONS = [
        'LIKE_POST', 'DISLIKE_POST', 'CREATE_POST', 'CREATE_COMMENT',
        'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER',
        'TREND', 'REFRESH', 'DO_NOTHING', 'FOLLOW', 'MUTE'
    ]

    # Report Agent configuration
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))

    @classmethod
    def validate(cls):
        """Validate required configuration"""
        errors = []
        if not cls.LLM_API_KEY:
            errors.append("LLM_API_KEY not configured (set to any non-empty value, e.g. 'ollama')")
        if not cls.NEO4J_URI:
            errors.append("NEO4J_URI not configured")
        if not cls.NEO4J_PASSWORD:
            errors.append("NEO4J_PASSWORD not configured")
        return errors
