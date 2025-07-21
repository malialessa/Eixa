import asyncio
import os
import logging
from datetime import datetime, timedelta, timezone # Adicionado timezone
import uuid # NOVO: Import para gerar UUIDs para o state
import json # NOVO: Import para json.loads

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.exceptions import RefreshError # NOVO: Para tratar erros de refresh

from firestore_client_singleton import _initialize_firestore_client_instance # Corrigido importação
from config import EIXA_GOOGLE_AUTH_COLLECTION # NOVO: Importa da configuração centralizada

# Configuração de logging
logging.basicConfig(level=logging.INFO)
CALENDAR_UTILS_LOGGER = logging.getLogger("CALENDAR_UTILS")

# Scopes necessários para acessar o Google Calendar.
# Para sincronização bidirecional completa, use 'https://www.googleapis.com/auth/calendar'
# Se for apenas leitura, 'calendar.readonly' é suficiente.
GOOGLE_CALENDAR_SCOPES = ['https://www.googleapis.com/auth/calendar'] # Mudei para read/write completo

class GoogleCalendarUtils:
    def __init__(self):
        # Corrigido: Obtenha a instância do Firestore via a função de inicialização
        self.db = _initialize_firestore_client_instance()

        # Carrega variáveis de ambiente para o OAuth 2.0 Client
        self.client_id = os.getenv('GOOGLE_CLIENT_ID')
        self.client_secret = os.getenv('GOOGLE_CLIENT_SECRET')
        self.redirect_uri = os.getenv('GOOGLE_REDIRECT_URI') # Ex: https://your-cloud-run-url.run.app/oauth2callback
        self.frontend_url = os.getenv('FRONTEND_URL') # NOVO: URL do frontend para javascript_origins

        if not all([self.client_id, self.client_secret, self.redirect_uri, self.frontend_url]):
            CALENDAR_UTILS_LOGGER.error("Uma ou mais variáveis de ambiente GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI ou FRONTEND_URL não configuradas para OAuth.")
            self.oauth_config_ready = False
        else:
            self.oauth_config_ready = True
            CALENDAR_UTILS_LOGGER.info("Configurações de OAuth do Google carregadas com sucesso.")

    async def _get_credentials_doc_ref(self, user_id: str):
        # Usar a constante do config.py
        return self.db.collection(EIXA_GOOGLE_AUTH_COLLECTION).document(user_id)

    async def _get_stored_credentials(self, user_id: str) -> dict | None:
        """Busca as credenciais do usuário no Firestore."""
        CALENDAR_UTILS_LOGGER.info(f"Buscando credenciais do Google para user_id: {user_id}")
        doc_ref = await self._get_credentials_doc_ref(user_id)
        doc = await asyncio.to_thread(doc_ref.get)
        if doc.exists:
            CALENDAR_UTILS_LOGGER.info(f"Credenciais encontradas para user_id: {user_id}")
            return doc.to_dict()
        CALENDAR_UTILS_LOGGER.warning(f"Nenhuma credencial encontrada para user_id: {user_id}")
        return None

    async def _save_credentials(self, user_id: str, credentials_data: dict):
        """Salva as credenciais atualizadas no Firestore."""
        CALENDAR_UTILS_LOGGER.info(f"Salvando/Atualizando credenciais do Google para user_id: {user_id}")
        doc_ref = await self._get_credentials_doc_ref(user_id)
        # Atenção: Considerar criptografar o refresh_token se a segurança for uma preocupação extrema.
        # Por enquanto, salvamos diretamente.
        await asyncio.to_thread(doc_ref.set, credentials_data)
        CALENDAR_UTILS_LOGGER.info(f"Credenciais salvas com sucesso para user_id: {user_id}")
    
    async def delete_credentials(self, user_id: str) -> dict:
        """Deleta as credenciais de um usuário do Firestore."""
        CALENDAR_UTILS_LOGGER.info(f"Deletando credenciais do Google para user_id: {user_id}")
        doc_ref = await self._get_credentials_doc_ref(user_id)
        try:
            await asyncio.to_thread(doc_ref.delete)
            CALENDAR_UTILS_LOGGER.info(f"Credenciais deletadas com sucesso para user_id: {user_id}.")
            return {"status": "success", "message": "Credenciais Google deletadas."}
        except Exception as e:
            CALENDAR_UTILS_LOGGER.error(f"Erro ao deletar credenciais para user_id: {user_id}: {e}", exc_info=True)
            return {"status": "error", "message": f"Falha ao deletar credenciais: {str(e)}"}


    async def get_credentials(self, user_id: str) -> Credentials | None:
        """
        Obtém credenciais do Firestore e as refresca se necessário.
        Retorna um objeto Credentials ou None se não houver credenciais válidas.
        """
        if not self.oauth_config_ready:
            CALENDAR_UTILS_LOGGER.error("Configurações de OAuth não prontas. Não é possível obter credenciais.")
            return None

        CALENDAR_UTILS_LOGGER.info(f"Obtendo e refrescando credenciais para user_id: {user_id}")
        stored_data = await self._get_stored_credentials(user_id)
        if not stored_data:
            CALENDAR_UTILS_LOGGER.warning(f"Não foi possível obter credenciais para {user_id}.")
            return None

        # Assegura que todos os campos necessários estejam presentes para Credentials.from_authorized_user_info
        required_fields = ['token', 'refresh_token', 'token_uri', 'client_id', 'client_secret', 'scopes']
        if not all(k in stored_data for k in required_fields):
            CALENDAR_UTILS_LOGGER.error(f"Dados de credenciais incompletos para user_id: {user_id}. Campos ausentes: {[k for k in required_fields if k not in stored_data]}")
            return None

        creds = Credentials.from_authorized_user_info(stored_data)

        # Atualiza client_id e client_secret com os valores das variáveis de ambiente
        # Isso é crucial para que o refresco funcione corretamente com o ambiente atual.
        creds.client_id = self.client_id
        creds.client_secret = self.client_secret
        
        if creds.expired and creds.refresh_token:
            CALENDAR_UTILS_LOGGER.info(f"Credenciais expiradas para {user_id}, tentando refrescar.")
            try:
                await asyncio.to_thread(creds.refresh, Request())
                CALENDAR_UTILS_LOGGER.info(f"Credenciais refrescadas com sucesso para {user_id}.")
                await self._save_credentials(user_id, json.loads(creds.to_json())) # Converte para dict antes de salvar
            except RefreshError as e: # Tratar especificamente erros de refresh
                CALENDAR_UTILS_LOGGER.error(f"Erro ao refrescar credenciais para {user_id}: {e}. Credenciais inválidas.", exc_info=True)
                # O token de refresh pode ter sido revogado ou expirado. Remover para forçar reautenticação.
                await self.delete_credentials(user_id)
                return None
            except Exception as e:
                CALENDAR_UTILS_LOGGER.error(f"Erro inesperado ao refrescar credenciais para {user_id}: {e}", exc_info=True)
                return None
        elif not creds.valid: # Se não expiradas, mas por algum motivo inválidas (e sem refresh_token, ou já tentou refresh)
            CALENDAR_UTILS_LOGGER.warning(f"Credenciais inválidas e não refrescáveis para {user_id}. Sugira reautenticação.")
            return None
        
        CALENDAR_UTILS_LOGGER.info(f"Credenciais válidas obtidas para {user_id}.")
        return creds

    # --- NOVA FUNÇÃO: Gerar URL de Autorização OAuth ---
    async def get_auth_url(self, user_id: str) -> str | None:
        """
        Gera e retorna a URL de autorização do Google para o usuário.
        Salva o 'state' no Firestore, associado ao user_id, para validação no callback.
        """
        if not self.oauth_config_ready:
            CALENDAR_UTILS_LOGGER.error("Configurações de OAuth não prontas. Não é possível gerar URL de autorização.")
            return None

        client_config = {
            "web": {
                "client_id": self.client_id,
                "project_id": os.getenv('GCP_PROJECT'), # Adicionado o project_id
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_secret": self.client_secret,
                "redirect_uris": [self.redirect_uri],
                "javascript_origins": [self.frontend_url] # O domínio do seu frontend
            }
        }
        
        flow = Flow.from_client_config(
            client_config, 
            scopes=GOOGLE_CALENDAR_SCOPES, 
            redirect_uri=self.redirect_uri
        )

        # O 'state' é crucial para segurança (CSRF). Deve ser único e validado no retorno.
        # Adicione o user_id no state para poder recuperá-lo no callback
        # Criptografar o state em um ambiente de produção é altamente recomendado.
        # Por simplicidade, concatenamos o user_id, mas para produção, use JWT ou outra forma segura.
        unique_state = f"{user_id}-{str(uuid.uuid4())}"

        authorization_url, state_from_flow = await asyncio.to_thread(
            flow.authorization_url,
            access_type='offline',  # Necessário para obter um refresh_token
            include_granted_scopes='true',
            state=unique_state # Passa o state que contém o user_id
        )
        
        # Salva o 'state' gerado no Firestore, associado ao user_id, para validação no callback.
        # Isso garante que a resposta OAuth veio de uma requisição que iniciamos.
        try:
            doc_ref = await self._get_credentials_doc_ref(user_id)
            await asyncio.to_thread(doc_ref.set, {"oauth_state": unique_state}, merge=True)
            CALENDAR_UTILS_LOGGER.info(f"OAuth state '{unique_state}' stored for user '{user_id}'.")
        except Exception as e:
            CALENDAR_UTILS_LOGGER.error(f"Falha ao salvar OAuth state para user '{user_id}': {e}", exc_info=True)
            return None # Não pode prosseguir se não salvou o state
        
        CALENDAR_UTILS_LOGGER.info(f"URL de autorização gerada para user_id: {user_id}. URL: {authorization_url[:100]}...")
        
        return authorization_url

    # --- NOVA FUNÇÃO: Processar o Callback OAuth ---
    async def handle_oauth2_callback(self, authorization_response_url: str) -> dict:
        """
        Processa a URL de resposta de autorização do Google.
        Troca o código por tokens e salva as credenciais no Firestore.
        Retorna um dicionário com status, mensagem e o user_id.
        """
        if not self.oauth_config_ready:
            CALENDAR_UTILS_LOGGER.error("Configurações de OAuth não prontas. Não é possível processar callback.")
            return {"status": "error", "message": "Configuração de OAuth incompleta no backend.", "user_id": None}
        
        # O 'state' está na URL de resposta. Precisamos extrair o user_id dele.
        # Usamos URLSearchParams para parsear a query string da URL.
        from urllib.parse import urlparse, parse_qs
        parsed_url = urlparse(authorization_response_url)
        query_params = parse_qs(parsed_url.query)
        
        state = query_params.get('state', [None])[0]
        code = query_params.get('code', [None])[0]
        error = query_params.get('error', [None])[0]

        if error:
            CALENDAR_UTILS_LOGGER.error(f"OAuth callback recebido com erro: {error}. State: {state}")
            return {"status": "error", "message": f"Autorização Google negada ou falhou: {error}.", "user_id": None}
        
        if not state:
            CALENDAR_UTILS_LOGGER.error("OAuth callback recebido sem 'state'. Possível ataque CSRF ou fluxo inválido.")
            return {"status": "error", "message": "Parâmetro de segurança ausente. Tente novamente.", "user_id": None}
        
        # Extrair user_id do state. Ele está no formato "user_id-UUID"
        try:
            user_id = state.split('-')[0]
            if not user_id: raise ValueError("User ID not found in state.")
        except Exception as e:
            CALENDAR_UTILS_LOGGER.error(f"Não foi possível extrair user_id do state '{state}': {e}", exc_info=True)
            return {"status": "error", "message": "Erro de segurança: ID de usuário inválido no state.", "user_id": None}

        # Validação do state no Firestore
        stored_doc_data = await self._get_stored_credentials(user_id)
        if not stored_doc_data or stored_doc_data.get("oauth_state") != state:
            CALENDAR_UTILS_LOGGER.warning(f"State recebido '{state}' não corresponde ao armazenado para user '{user_id}' ou não encontrado. Potencial CSRF ou reuso.")
            # Remover o state para evitar reuso, mesmo que não seja o mesmo
            doc_ref = await self._get_credentials_doc_ref(user_id)
            try: await asyncio.to_thread(doc_ref.update, {"oauth_state": firestore.DELETE_FIELD}) # Usa firestore.DELETE_FIELD
            except Exception as e: CALENDAR_UTILS_LOGGER.error(f"Erro ao remover 'oauth_state' para {user_id}: {e}")
            return {"status": "error", "message": "Validação de segurança falhou. Tente conectar novamente.", "user_id": user_id}

        # Remover o state após uso para evitar reuso (já que a validação passou)
        doc_ref = await self._get_credentials_doc_ref(user_id)
        try:
            from google.cloud import firestore # Importa aqui para usar DELETE_FIELD
            await asyncio.to_thread(doc_ref.update, {"oauth_state": firestore.DELETE_FIELD})
            CALENDAR_UTILS_LOGGER.info(f"OAuth state '{state}' removido para user '{user_id}' após validação.")
        except Exception as e:
            CALENDAR_UTILS_LOGGER.error(f"Erro ao remover 'oauth_state' para {user_id}: {e}")
            # Não é um erro fatal para o fluxo, apenas um aviso.

        # Re-cria o Flow com as mesmas configurações usadas para gerar a URL
        client_config = {
            "web": {
                "client_id": self.client_id,
                "project_id": os.getenv('GCP_PROJECT'),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_secret": self.client_secret,
                "redirect_uris": [self.redirect_uri],
                "javascript_origins": [self.frontend_url]
            }
        }
        flow = Flow.from_client_config(
            client_config, 
            scopes=GOOGLE_CALENDAR_SCOPES, 
            redirect_uri=self.redirect_uri
        )
        flow.redirect_url = self.redirect_uri # Garante que o redirect_url do flow está setado

        try:
            # Troca o código de autorização por tokens de acesso/refresh
            # authorization_response é a URL completa para a qual o Google redirecionou.
            await asyncio.to_thread(flow.fetch_token, authorization_response=authorization_response_url)

            creds = flow.credentials
            await self._save_credentials(user_id, json.loads(creds.to_json()))

            CALENDAR_UTILS_LOGGER.info(f"Credenciais OAuth para user_id: {user_id} salvas com sucesso no Firestore.")
            return {"status": "success", "message": "Conectado ao Google Calendar com sucesso!", "user_id": user_id}
        except Exception as e:
            CALENDAR_UTILS_LOGGER.error(f"Erro ao processar callback OAuth para user_id: {user_id}: {e}", exc_info=True)
            return {"status": "error", "message": f"Falha na autenticação com o Google: {str(e)}", "user_id": user_id}


    async def list_calendar_events(self, user_id: str, time_min: datetime, time_max: datetime, credentials: Credentials = None) -> list:
        """
        Lista eventos do Google Calendar para o usuário e período especificados.
        Pode receber credenciais diretamente ou buscá-las.
        Retorna uma lista de eventos brutos da API.
        """
        if credentials:
            creds = credentials
        else:
            creds = await self.get_credentials(user_id)
        
        if not creds:
            CALENDAR_UTILS_LOGGER.warning(f"Não há credenciais válidas para listar eventos para user_id: {user_id}")
            return []

        try:
            service = await asyncio.to_thread(build, 'calendar', 'v3', credentials=creds)

            CALENDAR_UTILS_LOGGER.info(f"Listando eventos do Google Calendar para user_id: {user_id} de {time_min.isoformat()} a {time_max.isoformat()}")
            
            time_min_utc = time_min.astimezone(timezone.utc) if time_min.tzinfo else time_min.replace(tzinfo=timezone.utc)
            time_max_utc = time_max.astimezone(timezone.utc) if time_max.tzinfo else time_max.replace(tzinfo=timezone.utc)

            events_result = await asyncio.to_thread(
                lambda: service.events().list(
                    calendarId='primary', 
                    timeMin=time_min_utc.isoformat().replace('+00:00', 'Z'),
                    timeMax=time_max_utc.isoformat().replace('+00:00', 'Z'),
                    maxResults=100, 
                    singleEvents=True,
                    orderBy='startTime'
                ).execute()
            )

            events = events_result.get('items', [])
            CALENDAR_UTILS_LOGGER.info(f"Encontrados {len(events)} eventos do Google Calendar para user_id: {user_id}")
            return events

        except HttpError as error:
            CALENDAR_UTILS_LOGGER.error(f"HttpError ao listar eventos do Google Calendar para {user_id}: {error.resp.status} - {error.content}", exc_info=True)
            if error.resp.status in [401, 403]:
                CALENDAR_UTILS_LOGGER.warning(f"Credenciais possivelmente inválidas ou revogadas para {user_id}. Sugira reautenticação.")
                # Tenta remover as credenciais para forçar o frontend a reautenticar
                await self.delete_credentials(user_id)
            return []
        except Exception as e:
            CALENDAR_UTILS_LOGGER.error(f"Erro inesperado ao listar eventos do Google Calendar para {user_id}: {e}", exc_info=True)
            return []

    # --- Funções para Sincronização Bidirecional (Futuro) ---
    async def create_calendar_event(self, user_id: str, event_data: dict) -> dict | None:
        """Cria um evento no Google Calendar."""
        CALENDAR_UTILS_LOGGER.info(f"Tentando criar evento no Google Calendar para user_id: {user_id}")
        creds = await self.get_credentials(user_id)
        if not creds:
            CALENDAR_UTILS_LOGGER.warning(f"Não há credenciais válidas para criar evento para user_id: {user_id}")
            return None
        try:
            service = await asyncio.to_thread(build, 'calendar', 'v3', credentials=creds)
            event = await asyncio.to_thread(
                lambda: service.events().insert(calendarId='primary', body=event_data).execute()
            )
            CALENDAR_UTILS_LOGGER.info(f"Evento criado no Google Calendar: {event.get('htmlLink')}")
            return event
        except HttpError as error:
            CALENDAR_UTILS_LOGGER.error(f"Erro ao criar evento do Google Calendar para {user_id}: {error.content}", exc_info=True)
            return None
        except Exception as e:
            CALENDAR_UTILS_LOGGER.error(f"Erro inesperado ao criar evento do Google Calendar para {user_id}: {e}", exc_info=True)
            return None

    async def update_calendar_event(self, user_id: str, event_id: str, event_data: dict) -> dict | None:
        """Atualiza um evento no Google Calendar."""
        CALENDAR_UTILS_LOGGER.info(f"Tentando atualizar evento {event_id} no Google Calendar para user_id: {user_id}")
        creds = await self.get_credentials(user_id)
        if not creds:
            CALENDAR_UTILS_LOGGER.warning(f"Não há credenciais válidas para atualizar evento para user_id: {user_id}")
            return None
        try:
            service = await asyncio.to_thread(build, 'calendar', 'v3', credentials=creds)
            event = await asyncio.to_thread(
                lambda: service.events().update(calendarId='primary', eventId=event_id, body=event_data).execute()
            )
            CALENDAR_UTILS_LOGGER.info(f"Evento {event_id} atualizado no Google Calendar: {event.get('htmlLink')}")
            return event
        except HttpError as error:
            CALENDAR_UTILS_LOGGER.error(f"Erro ao atualizar evento {event_id} do Google Calendar para {user_id}: {error.content}", exc_info=True)
            return None
        except Exception as e:
            CALENDAR_UTILS_LOGGER.error(f"Erro inesperado ao atualizar evento {event_id} do Google Calendar para {user_id}: {e}", exc_info=True)
            return None

    async def delete_calendar_event(self, user_id: str, event_id: str):
        """Deleta um evento no Google Calendar."""
        CALENDAR_UTILS_LOGGER.info(f"Tentando deletar evento {event_id} do Google Calendar para user_id: {user_id}")
        creds = await self.get_credentials(user_id)
        if not creds:
            CALENDAR_UTILS_LOGGER.warning(f"Não há credenciais válidas para deletar evento para user_id: {user_id}")
            return False
        try:
            service = await asyncio.to_thread(build, 'calendar', 'v3', credentials=creds)
            await asyncio.to_thread(
                lambda: service.events().delete(calendarId='primary', eventId=event_id).execute()
            )
            CALENDAR_UTILS_LOGGER.info(f"Evento {event_id} deletado do Google Calendar.")
            return True
        except HttpError as error:
            CALENDAR_UTILS_LOGGER.error(f"Erro ao deletar evento {event_id} do Google Calendar para {user_id}: {error.content}", exc_info=True)
            return False
        except Exception as e:
            CALENDAR_UTILS_LOGGER.error(f"Erro inesperado ao deletar evento {event_id} do Google Calendar para {user_id}: {e}", exc_info=True)
            return False