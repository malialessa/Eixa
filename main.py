import os
import json
import functions_framework # Para Cloud Functions/Run entry point
import logging
import time
import asyncio
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

# Configuração de logging no nível mais alto para toda a aplicação
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Bloco de Inicialização Crítico da Aplicação ---
try:
    # Importa os Orquestradores principais da lógica de negócio.
    from eixa_orchestrator import orchestrate_eixa_response
    from crud_orchestrator import orchestrate_crud_action

    app = Flask(__name__)
    CORS(app) 

    GCP_PROJECT = os.environ.get("GCP_PROJECT")
    REGION = os.environ.get("REGION", "us-central1") 
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") 
    GEMINI_TEXT_MODEL = os.environ.get("GEMINI_TEXT_MODEL", "gemini-2.5-flash")
    GEMINI_VISION_MODEL = os.environ.get("GEMINI_VISION_MODEL", "gemini-pro-vision") 

    if not GCP_PROJECT:
        logger.critical("Variável de ambiente 'GCP_PROJECT' não definida. A aplicação não funcionará corretamente.")
        raise EnvironmentError("GCP_PROJECT environment variable is not set. Please configure it.")
    
    logger.info(f"Aplicação EIXA inicializada com sucesso. GCP Project: {GCP_PROJECT}, Region: {REGION}.")

except Exception as e:
    logger.critical(f"FALHA FATAL DURANTE A INICIALIZAÇÃO DO MÓDULO PRINCIPAL: {e}", exc_info=True)
    raise

@app.before_request
def log_request_info():
    if request.method != 'OPTIONS':
        logger.info(json.dumps({
            "event": "http_request_received",
            "method": request.method,
            "path": request.path,
            "remote_addr": request.remote_addr,
            "headers_snippet": {k: v for k, v in request.headers.items() if k.lower() in ['user-agent', 'x-forwarded-for', 'x-cloud-trace-context']}
        }))

@functions_framework.http
def eixa_entry(request):
    start_time = time.time()

    headers = {
        'Access-Control-Allow-Origin': '*', 
        'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization', 
        'Access-Control-Max-Age': '3600'
    }

    if request.method == 'OPTIONS':
        return Response(status=204, headers=headers)

    if request.method != 'POST':
        logger.warning(f"Método HTTP não permitido: '{request.method}'. Requer POST.")
        return jsonify({"status": "error", "response": "Método não permitido. Use POST."}), 405, headers

    request_json = request.get_json(silent=True)
    if not request_json:
        logger.error("Corpo da requisição inválido ou JSON vazio.")
        return jsonify({"status": "error", "response": "Corpo da requisição inválido ou JSON vazio."}), 400, headers

    user_id = request_json.get('user_id')
    request_type = request_json.get('request_type')
    debug_mode = request_json.get('debug_mode', False)

    if not user_id or not isinstance(user_id, str):
        logger.error(f"Validação falhou: 'user_id' ausente ou inválido. Recebido: '{user_id}'.")
        return jsonify({"status": "error", "response": "O campo 'user_id' é obrigatório e deve ser uma string."}), 400, headers

    if not request_type:
        logger.error(f"Validação falhou: 'request_type' ausente. Recebido: '{request_type}'.")
        return jsonify({"status": "error", "response": "O campo 'request_type' é obrigatório."}), 400, headers

    logger.info(json.dumps({"event": "request_payload_details", "user_id": user_id, "request_type": request_type, "debug_mode": debug_mode}))

    try:
        response_payload = {}
        if request_type in ['chat_and_view', 'view_data']:
            message = request_json.get('message')
            uploaded_file = request_json.get('uploaded_file_data')
            view_request = request_json.get('view_request')

            response_payload = asyncio.run(orchestrate_eixa_response(
                user_id=user_id,
                user_message=message,
                uploaded_file_data=uploaded_file,
                view_request=view_request,
                gcp_project_id=GCP_PROJECT,
                region=REGION,
                gemini_api_key=GEMINI_API_KEY,
                gemini_text_model=GEMINI_TEXT_MODEL,
                gemini_vision_model=GEMINI_VISION_MODEL,
                firestore_collection_interactions='interactions', 
                debug_mode=debug_mode
            ))

        elif request_type == 'crud_action':
            response_payload = asyncio.run(orchestrate_crud_action(request_json))

        else:
            logger.error(f"Tipo de requisição inválido recebido: '{request_type}'.")
            return jsonify({"status": "error", "response": f"Tipo de requisição inválido: '{request_type}'."}), 400, headers

        end_time = time.time()
        duration = end_time - start_time
        logger.info(json.dumps({
            "event": "request_completed",
            "user_id": user_id,
            "request_type": request_type,
            "duration_seconds": f"{duration:.2f}",
            "response_status": response_payload.get("status", "unknown"),
            "message_snippet": response_payload.get("response", "")[:100]
        }))

        return jsonify(response_payload), 200, headers

    except Exception as e:
        end_time = time.time()
        duration = end_time - start_time
        error_message_for_user = "Ocorreu um erro interno inesperado. Por favor, tente novamente mais tarde."
        
        logger.error(json.dumps({
            "event": "orchestration_failed",
            "user_id": user_id,
            "request_type": request_type,
            "duration_seconds": f"{duration:.2f}",
            "error_type": type(e).__name__,
            "error_message": str(e)
        }), exc_info=True)

        return jsonify({"status": "error", "response": error_message_for_user, "debug_info": [f"Erro interno: {type(e).__name__} - {str(e)}"]}), 500, headers

if __name__ == '__main__':
    os.environ["GCP_PROJECT"] = os.environ.get("GCP_PROJECT", "your-local-gcp-project-id")
    os.environ["REGION"] = os.environ.get("REGION", "us-central1")
    
    port = int(os.environ.get('PORT', 8080))

    logger.info(f"Iniciando servidor Flask local na porta {port} para desenvolvimento...")
    app.run(debug=True, host='0.0.0.0', port=port)