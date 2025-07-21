import os
import json
import logging
import time
import asyncio
import functions_framework
from flask import Flask, request, jsonify, Response, redirect, url_for # ADDED redirect, url_for
from flask_cors import CORS

# === Google OAuth Imports ===
# Estas bibliotecas são essenciais para o fluxo OAuth 2.0 com o Google.
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
# === END Google OAuth Imports ===

# === EIXA Logic Imports ===
# Verifique que estes caminhos estão corretos
from eixa_orchestrator import orchestrate_eixa_response
from crud_orchestrator import orchestrate_crud_action
# Importe EIXA_GOOGLE_AUTH_COLLECTION para referência na coleção de credenciais.
from config import GEMINI_TEXT_MODEL, GEMINI_VISION_MODEL, EIXA_GOOGLE_AUTH_COLLECTION
# Importe GoogleCalendarUtils e SCOPES para gerenciar credenciais e definir permissões.
from google_calendar_utils import GoogleCalendarUtils, GOOGLE_CALENDAR_SCOPES

# === Inicialização do Logger ===
# Mantendo DEBUG para depuração local. Altere para INFO/WARNING em produção.
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === Instância do Flask ===
app = Flask(__name__)
# Habilita CORS para todas as rotas. Em produção, considere restringir Access-Control-Allow-Origin
# para os domínios específicos do seu frontend.
CORS(app)

# === Carregamento de variáveis do ambiente ===
GCP_PROJECT = os.environ.get("GCP_PROJECT")
REGION = os.environ.get("REGION", "us-central1")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# === NOVAS VARIÁVEIS DE AMBIENTE PARA GOOGLE OAUTH ===
# Estas devem ser configuradas no Cloud Run/Functions.
# GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET são do seu projeto GCP (Credenciais -> OAuth 2.0 Client IDs).
# GOOGLE_REDIRECT_URI DEVE ser o URL COMPLETO do seu endpoint /oauth2callback (ex: https://<SEU_CLOUD_RUN_URL>/oauth2callback).
# FRONTEND_URL é o domínio do seu frontend, usado para redirecionamentos após autenticação.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173") # Default para ambiente de dev local

if not GCP_PROJECT:
    logger.critical("Variável de ambiente 'GCP_PROJECT' não definida. A aplicação pode não funcionar.")
    raise EnvironmentError("GCP_PROJECT environment variable is not set.")
if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET or not GOOGLE_REDIRECT_URI:
    logger.warning("Uma ou mais variáveis de ambiente do Google OAuth (GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI) não estão definidas. A integração com o Google Calendar pode não funcionar corretamente.")

logger.info(f"Aplicação EIXA inicializada. GCP Project: {GCP_PROJECT}, Region: {REGION}")
logger.info(f"Google OAuth Config: Client ID present: {bool(GOOGLE_CLIENT_ID)}, Redirect URI present: {bool(GOOGLE_REDIRECT_URI)}")

# === Instância do Google Calendar Utils ===
# Esta instância será usada para interagir com o Firestore para salvar/buscar credenciais.
google_calendar_utils_instance = GoogleCalendarUtils()

@app.before_request
def log_request_info():
    """Registra informações básicas de cada requisição HTTP recebida."""
    if request.method != 'OPTIONS':
        logger.debug(json.dumps({
            "event": "http_request_received",
            "method": request.method,
            "path": request.path,
            "remote_addr": request.remote_addr,
            "headers_snippet": {k: v for k, v in request.headers.items() if k.lower() in ['user-agent', 'x-forwarded-for', 'x-cloud-trace-context']}
        }))

# === Rota de Health Check (GET para a raiz) ===
@app.route("/", methods=["GET"])
def root_check():
    """Endpoint simples para verificar se a aplicação está no ar."""
    logger.debug("Health check requested.")
    return jsonify({"status": "ok", "message": "EIXA está no ar. Use /interact para interagir."}), 200

# === NOVA ROTA: Iniciar o fluxo de autenticação do Google Calendar ===
@app.route("/auth/google", methods=["GET"])
async def google_auth():
    """
    Inicia o fluxo OAuth 2.0 para o Google Calendar.
    O frontend deve chamar este endpoint com o `user_id` como query parameter.
    """
    user_id = request.args.get('user_id')
    if not user_id:
        logger.error("google_auth: Missing user_id for OAuth initiation.")
        return jsonify({"status": "error", "message": "Parâmetro 'user_id' é obrigatório para iniciar a autenticação Google."}), 400

    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET or not GOOGLE_REDIRECT_URI:
        logger.critical("Google OAuth environment variables are not set for /auth/google. Cannot proceed with authentication.")
        return jsonify({"status": "error", "message": "Erro de configuração do servidor para autenticação Google. Contate o suporte."}), 500

    # Configuração do cliente OAuth. `javascript_origins` é crucial para CORS no Google.
    client_config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [GOOGLE_REDIRECT_URI],
            "javascript_origins": [FRONTEND_URL] # O domínio do seu frontend
        }
    }

    # Cria o objeto Flow para o fluxo de autenticação.
    flow = Flow.from_client_config(
        client_config,
        scopes=GOOGLE_CALENDAR_SCOPES,
        redirect_uri=GOOGLE_REDIRECT_URI
    )

    # Gera a URL de autorização. 'state' é para segurança CSRF.
    # access_type='offline' garante que você obtenha um refresh_token.
    # include_granted_scopes='true' garante que a URL solicite todos os scopes necessários.
    authorization_url, state = await asyncio.to_thread(flow.authorization_url, access_type='offline', include_granted_scopes='true')
    
    # Armazena o 'state' no Firestore, associado ao user_id, para validação no callback.
    # ISSO É ESSENCIAL PARA SEGURANÇA. O user_id é o ID do documento.
    # O Firestore `merge=True` é importante para não sobrescrever outros dados do documento.
    try:
        doc_ref = google_calendar_utils_instance.db.collection(EIXA_GOOGLE_AUTH_COLLECTION).document(user_id)
        await asyncio.to_thread(doc_ref.set, {"oauth_state": state}, merge=True)
        logger.info(f"google_auth: OAuth state '{state}' stored for user '{user_id}'.")
    except Exception as e:
        logger.error(f"google_auth: Failed to save OAuth state for user '{user_id}': {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Erro interno ao preparar autenticação."}), 500

    logger.info(f"google_auth: Generated authorization URL for user {user_id}. Returning URL.")
    # Retorna a URL para o frontend. O frontend DEVE redirecionar o usuário para esta URL.
    return jsonify({"auth_url": authorization_url}), 200

# === NOVA ROTA: Callback para o Google OAuth ===
@app.route("/oauth2callback", methods=["GET"])
async def oauth2callback():
    """
    Recebe o redirecionamento do Google após a autorização do usuário.
    Troca o 'code' por tokens de acesso e refresh, e os salva no Firestore.
    """
    state = request.args.get('state')
    code = request.args.get('code')
    error = request.args.get('error')

    # No fluxo real, o 'user_id' precisa ser recuperado com base no 'state' ou passado de volta pelo frontend.
    # Por simplicidade e robustez para Cloud Run, vamos assumir que o frontend pode passar o user_id
    # como um query param adicional na GOOGLE_REDIRECT_URI (ex: https://your.app/oauth2callback?user_id=<user_id>)
    # Se você não fizer isso, o 'user_id_from_state' abaixo é uma tentativa falha de recuperação segura.
    # A forma mais robusta é o 'state' conter o 'user_id' e ser validado cryptograficamente.
    
    user_id_from_state = None
    if state:
        try:
            db = google_calendar_utils_instance.db
            # Busca o user_id que corresponde a este state. (Idealmente, isso seria um índice de documento, não uma query de coleção)
            # Para produção, o `state` deveria ser criptográfico e conter o user_id.
            docs = await asyncio.to_thread(lambda: list(db.collection(EIXA_GOOGLE_AUTH_COLLECTION).where("oauth_state", "==", state).limit(1).stream()))
            if docs:
                user_id_from_state = docs[0].id # O ID do documento é o user_id
                # Após usar o state, ele deve ser invalidado/removido para evitar reuso.
                # Remove o campo 'oauth_state' do documento do user_id.
                doc_ref = db.collection(EIXA_GOOGLE_AUTH_COLLECTION).document(user_id_from_state)
                await asyncio.to_thread(doc_ref.update, {"oauth_state": firestore.DELETE_FIELD})
                logger.debug(f"oauth2callback: OAuth state '{state}' removed for user '{user_id_from_state}'.")
            else:
                logger.warning(f"oauth2callback: State '{state}' not found in Firestore. Potential CSRF or invalid state.")
        except Exception as e:
            logger.error(f"oauth2callback: Error retrieving user_id from state or deleting state: {e}", exc_info=True)

    # Prioriza o user_id se veio diretamente na URL, senão tenta do state.
    # O frontend deve ser instruído a adicionar `&user_id=<user_id>` ao final da `GOOGLE_REDIRECT_URI` ao redirecionar.
    user_id = request.args.get('user_id') or user_id_from_state 

    if error:
        logger.error(f"oauth2callback: Google OAuth authorization denied or failed for user_id: {user_id}. Error: {error}")
        return redirect(f"{FRONTEND_URL}/dashboard?auth_status=error&message=Autenticação%20Google%20falhou")

    if not code:
        logger.error(f"oauth2callback: No authorization code received for user_id: {user_id}")
        return redirect(f"{FRONTEND_URL}/dashboard?auth_status=error&message=Código%20de%20autorização%20não%20recebido")

    if not user_id:
        logger.critical("oauth2callback: User ID not identified. Cannot save credentials.")
        return redirect(f"{FRONTEND_URL}/dashboard?auth_status=error&message=Erro%20interno:%20ID%20de%20usuário%20não%20identificado")

    # Reconstruir o objeto Flow para trocar o código por tokens.
    client_config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [GOOGLE_REDIRECT_URI],
            "javascript_origins": [FRONTEND_URL]
        }
    }
    flow = Flow.from_client_config(
        client_config,
        scopes=GOOGLE_CALENDAR_SCOPES,
        redirect_uri=GOOGLE_REDIRECT_URI
    )
    # Importante: O redirect_url do objeto flow precisa ser definido para a URL correta do callback
    flow.redirect_url = GOOGLE_REDIRECT_URI
    
    try:
        # Troca o código de autorização por tokens de acesso e refresh.
        # Esta é uma operação de rede síncrona, deve ser encapsulada em asyncio.to_thread.
        token_response = await asyncio.to_thread(flow.fetch_token, code=code)
        
        creds = flow.credentials
        
        # Salva as credenciais no Firestore para uso futuro pela EIXA.
        # `creds.to_json()` contém token, refresh_token, token_uri, client_id, client_secret, scopes, etc.
        await google_calendar_utils_instance._save_credentials(user_id, json.loads(creds.to_json()))
        
        logger.info(f"oauth2callback: Successfully obtained and saved Google Calendar credentials for user: {user_id}")
        # Redireciona o usuário de volta para o frontend com um status de sucesso.
        return redirect(f"{FRONTEND_URL}/dashboard?auth_status=success&message=Google%20Calendar%20conectado%20com%20sucesso")

    except Exception as e:
        logger.critical(f"oauth2callback: Failed to exchange code for tokens or save credentials for user {user_id}: {e}", exc_info=True)
        # Redireciona o usuário de volta para o frontend com um status de erro.
        return redirect(f"{FRONTEND_URL}/dashboard?auth_status=error&message=Falha%20ao%20conectar%20Google%20Calendar")

# === Rota principal da API (POST e OPTIONS para /interact) ===
@app.route("/interact", methods=["POST", "OPTIONS"])
async def interact_api():
    """
    Ponto de entrada principal para todas as interações da EIXA (chat, CRUD, visualizações).
    """
    start_time = time.time()
    logger.debug("interact_api: Function started.")

    headers = {
        'Access-Control-Allow-Origin': '*', # Em produção, defina o domínio do seu frontend
        'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization', # CORRIGIDO: Access-Control-Allow-Headers
        'Access-Control-Max-Age': '3600'
    }

    if request.method == 'OPTIONS':
        logger.debug("interact_api: OPTIONS request received.")
        return Response(status=204, headers=headers)

    request_json = request.get_json(silent=True)
    if not request_json:
        logger.error("interact_api: Invalid request body or empty JSON.")
        return jsonify({"status": "error", "response": "Corpo da requisição inválido ou JSON vazio."}), 400, headers

    user_id = request_json.get('user_id')
    request_type = request_json.get('request_type')
    debug_mode = request_json.get('debug_mode', False)

    logger.debug(f"interact_api: Received user_id='{user_id}', request_type='{request_type}', debug_mode='{debug_mode}'.")

    if not user_id or not isinstance(user_id, str):
        logger.error(f"interact_api: Missing or invalid user_id: '{user_id}'.")
        return jsonify({"status": "error", "response": "O campo 'user_id' é obrigatório e deve ser uma string."}), 400, headers

    if not request_type:
        logger.error(f"interact_api: Missing request_type.")
        return jsonify({"status": "error", "response": "O campo 'request_type' é obrigatório."}), 400, headers

    try:
        if request_type in ['chat_and_view', 'view_data']:
            logger.debug(f"interact_api: Calling orchestrate_eixa_response for request_type: {request_type}")
            response_payload = await orchestrate_eixa_response(
                user_id=user_id,
                user_message=request_json.get('message'),
                uploaded_file_data=request_json.get('uploaded_file_data'),
                view_request=request_json.get('view_request'),
                gcp_project_id=GCP_PROJECT,
                region=REGION,
                gemini_api_key=GEMINI_API_KEY,
                gemini_text_model=GEMINI_TEXT_MODEL,
                gemini_vision_model=GEMINI_VISION_MODEL,
                firestore_collection_interactions='interactions',
                debug_mode=debug_mode
            )
        elif request_type == 'crud_action':
            logger.debug(f"interact_api: Calling orchestrate_crud_action for request_type: {request_type}. Payload: {request_json}")
            response_payload = await orchestrate_crud_action(request_json)
        else:
            logger.error(f"interact_api: Invalid request type received: '{request_type}'.")
            return jsonify({"status": "error", "response": f"Tipo de requisição inválido: '{request_type}'."}), 400, headers

        duration = time.time() - start_time
        logger.info(json.dumps({
            "event": "request_completed",
            "user_id": user_id,
            "request_type": request_type,
            "duration_seconds": f"{duration:.2f}",
            "response_status": response_payload.get("status", "unknown"),
        }))
        logger.debug(f"interact_api: Response payload being sent: {response_payload}")

        return jsonify(response_payload), 200, headers

    except Exception as e:
        duration = time.time() - start_time
        logger.critical(json.dumps({
            "event": "orchestration_failed",
            "user_id": user_id,
            "request_type": request_type,
            "duration_seconds": f"{duration:.2f}",
            "error_type": type(e).__name__,
            "error_message": str(e)
        }), exc_info=True)

        return jsonify({
            "status": "error",
            "response": "Erro interno inesperado.",
            "debug_info": [f"Erro interno: {type(e).__name__} - {str(e)}"]
        }), 500, headers

# === Função de entrada do functions_framework para o Cloud Run ===
@functions_framework.http
def eixa_entry(request):
    """
    Função de entrada para o Google Cloud Run/Functions.
    Ela encapsula a aplicação Flask e lida com o ciclo de vida da requisição.
    """
    logger.debug("eixa_entry: Request received by functions_framework wrapper.")
    # Flask é uma aplicação WSGI. functions_framework.http pode encapsular
    # diretamente a instância Flask. As rotas assíncronas são manipuladas
    # pela configuração subjacente do Cloud Run (Gunicorn com workers assíncronos).
    return app(request.environ, lambda status, headers: [])

# === Execução local para testes ===
if __name__ == '__main__':
    # Configura variáveis de ambiente para o ambiente de desenvolvimento local.
    os.environ["GCP_PROJECT"] = os.environ.get("GCP_PROJECT", "local-dev-project")
    os.environ["REGION"] = os.environ.get("REGION", "us-central1")
    os.environ["GEMINI_API_KEY"] = os.environ.get("GEMINI_API_KEY", "YOUR_LOCAL_GEMINI_API_KEY")
    
    # Configure estas variáveis com suas próprias credenciais do Google Cloud Console
    # e a URL de redirecionamento para o seu ambiente local.
    # Ex: http://localhost:8080/oauth2callback
    os.environ["GOOGLE_CLIENT_ID"] = os.environ.get("GOOGLE_CLIENT_ID", "YOUR_GOOGLE_CLIENT_ID")
    os.environ["GOOGLE_CLIENT_SECRET"] = os.environ.get("GOOGLE_CLIENT_SECRET", "YOUR_GOOGLE_CLIENT_SECRET")
    os.environ["GOOGLE_REDIRECT_URI"] = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:8080/oauth2callback")
    # URL do frontend para redirecionamentos.
    os.environ["FRONTEND_URL"] = os.environ.get("FRONTEND_URL", "http://localhost:5173") 
    
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"Iniciando localmente na porta {port}")
    # Quando executado diretamente (python main.py), o Flask usa seu servidor de desenvolvimento.
    # Para Cloud Run, o `eixa_entry` é o ponto de entrada.
    app.run(debug=True, host='0.0.0.0', port=port)