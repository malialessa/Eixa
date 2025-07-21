import os

TOP_LEVEL_COLLECTIONS_MAP = {
    'eixa_user_data': 'eixa_users',
    'interactions': 'eixa_user_interactions',
    'profiles': 'eixa_profiles', 
    'flags': 'eixa_user_flags',
    'behavior': 'eixa_user_behaviors',
    'pending_actions': 'eixa_pending_actions',
    'embeddings': 'eixa_embeddings',
    'nudger': 'eixa_nudger_state',
    'self_eval': 'eixa_self_eval',
    'memories': 'eixa_emotional_memories',
    # --- NOVAS COLEÇÕES PARA GERENCIAMENTO DE TEMPO E INTEGRAÇÃO ---
    'routines': 'eixa_routines', # Coleção para templates de rotinas por usuário
    'google_auth': 'eixa_google_auth', # Coleção para tokens de autenticação do Google Calendar por usuário
    # ----------------------------------------------------------------
}

SUBCOLLECTIONS_MAP = {
    # 'agenda' agora armazenará tarefas com 'time' (HH:MM) e 'duration_minutes'
    'agenda': 'agenda', 
    'projects': 'projects',
    'checkpoints': 'self_checkpoints',
    'vector_memory': 'vector_memory'
}

USERS_COLLECTION = TOP_LEVEL_COLLECTIONS_MAP['eixa_user_data']
EIXA_INTERACTIONS_COLLECTION = TOP_LEVEL_COLLECTIONS_MAP['interactions']
EIXA_ROUTINES_COLLECTION = TOP_LEVEL_COLLECTIONS_MAP['routines'] # Novo atalho
EIXA_GOOGLE_AUTH_COLLECTION = TOP_LEVEL_COLLECTIONS_MAP['google_auth'] # Novo atalho

GEMINI_TEXT_MODEL        = "gemini-2.5-flash"
GEMINI_VISION_MODEL      = "gemini-1.5-flash" 

DEFAULT_TEMPERATURE      = 0.4

CONVERSATION_HARD_LIMIT_TOKENS = 8000
MAX_PROMPT_TOKENS_BUDGET       = 8192
DEFAULT_MAX_OUTPUT_TOKENS      = 4096

DEFAULT_TIMEZONE           = os.getenv('DEFAULT_TIMEZONE', 'America/Sao_Paulo')
DEFAULT_TIMEOUT_SECONDS    = 30
CONFIG_SCHEMA_VERSION      = "2.0"

EMBEDDING_MODEL_NAME = "text-embedding-004"

CHROMA_DB_PATH = "chroma_db"

# --- Variáveis de ambiente para o Google OAuth (podem ser necessárias no futuro, dependendo do fluxo) ---
# GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
# GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')
# GOOGLE_REDIRECT_URI = os.getenv('GOOGLE_REDIRECT_URI')
# ----------------------------------------------------------------------------------------------------