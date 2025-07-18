import os

TOP_LEVEL_COLLECTIONS_MAP = {
    'eixa_user_data': 'eixa_users',
    'interactions': 'eixa_user_interactions',
    'profiles': 'eixa_profiles', 
    'flags': 'eixa_user_flags',
    'behavior': 'eixa_user_behaviors',
    'pending': 'eixa_pending_actions',
    'embeddings': 'eixa_embeddings',
    'nudger': 'eixa_nudger_state',
    'self_eval': 'eixa_self_eval',
    'memories': 'eixa_emotional_memories',
}

SUBCOLLECTIONS_MAP = {
    'agenda': 'agenda',
    'projects': 'projects',
    'checkpoints': 'self_checkpoints',
    'vector_memory': 'vector_memory'
}

USERS_COLLECTION = TOP_LEVEL_COLLECTIONS_MAP['eixa_user_data']
EIXA_INTERACTIONS_COLLECTION = TOP_LEVEL_COLLECTIONS_MAP['interactions']

GEMINI_TEXT_MODEL        = "gemini-2.5-flash"
# CORREÇÃO CRÍTICA: Atualizar o modelo de visão para um modelo suportado pela Google
GEMINI_VISION_MODEL      = "gemini-1.5-flash" # RECOMENDADO: Ou "gemini-1.5-pro", se disponível e for mais adequado

DEFAULT_TEMPERATURE      = 0.4

CONVERSATION_HARD_LIMIT_TOKENS = 8000
MAX_PROMPT_TOKENS_BUDGET       = 8192 # Limite de tokens para o prompt (input do LLM)
DEFAULT_MAX_OUTPUT_TOKENS      = 4096 # Limite máximo de tokens que o LLM deve gerar

DEFAULT_TIMEZONE           = os.getenv('DEFAULT_TIMEZONE', 'America/Sao_Paulo')
DEFAULT_TIMEOUT_SECONDS    = 30
CONFIG_SCHEMA_VERSION      = "2.0"

EMBEDDING_MODEL_NAME = "text-embedding-004"

CHROMA_DB_PATH = "chroma_db"