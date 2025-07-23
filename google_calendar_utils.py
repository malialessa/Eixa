# --- START OF FILE google_calendar_utils.py ---

import asyncio
import os
import logging
from datetime import datetime, timedelta, timezone
import uuid
from urllib.parse import urlparse, parse_qs
import json # Adicionado 'json' para creds.to_json() e json.loads()

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.exceptions import RefreshError

from firestore_client_singleton import _initialize_firestore_client_instance
from config import EIXA_GOOGLE_AUTH_COLLECTION

logging.basicConfig(level=logging.INFO)
CALENDAR_UTILS_LOGGER = logging.getLogger("CALENDAR_UTILS")

GOOGLE_CALENDAR_SCOPES = ['https://www.googleapis.com/auth/calendar']

class GoogleCalendarUtils:
    def __init__(self):
        self.db = _initialize_firestore_client_instance()

        self.client_id = os.getenv('GOOGLE_CLIENT_ID')
        self.client_secret = os.getenv('GOOGLE_CLIENT_SECRET')
        self.redirect_uri = os.getenv('GOOGLE_REDIRECT_URI')
        self.frontend_url = os.getenv('FRONTEND_URL')

        if not all([self.client_id, self.client_secret, self.redirect_uri, self.frontend_url]):
            CALENDAR_UTILS_LOGGER.error("Uma ou mais variáveis de ambiente GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI ou FRONTEND_URL não configuradas para OAuth.")
            self.oauth_config_ready = False
        else:
            self.oauth_config_ready = True
            CALENDAR_UTILS_LOGGER.info("Configurações de OAuth do Google carregadas com sucesso.")

    async def _get_credentials_doc_ref(self, user_id: str):
        return self.db.collection(EIXA_GOOGLE_AUTH_COLLECTION).document(user_id)

    async def _get_stored_credentials(self, user_id: str) -> dict | None:
        CALENDAR_UTILS_LOGGER.info(f"Buscando credenciais do Google para user_id: {user_id}")
        doc_ref = await self._get_credentials_doc_ref(user_id)
        doc = await asyncio.to_thread(doc_ref.get)
        if doc.exists:
            CALENDAR_UTILS_LOGGER.info(f"Credenciais encontradas para user_id: {user_id}")
            return doc.to_dict()
        CALENDAR_UTILS_LOGGER.warning(f"Nenhuma credencial encontrada para user_id: {user_id}")
        return None

    async def _save_credentials(self, user_id: str, credentials_data: dict):
        CALENDAR_UTILS_LOGGER.info(f"Salvando/Atualizando credenciais do Google para user_id: {user_id}")
        doc_ref = await self._get_credentials_doc_ref(user_id)
        await asyncio.to_thread(doc_ref.set, credentials_data)
        CALENDAR_UTILS_LOGGER.info(f"Credenciais salvas com sucesso para user_id: {user_id}")
    
    async def delete_credentials(self, user_id: str) -> dict:
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
        if not self.oauth_config_ready:
            CALENDAR_UTILS_LOGGER.error("Configurações de OAuth não prontas. Não é possível obter credenciais.")
            return None

        CALENDAR_UTILS_LOGGER.info(f"Obtendo e refrescando credenciais para user_id: {user_id}")
        stored_data = await self._get_stored_credentials(user_id)
        if not stored_data:
            CALENDAR_UTILS_LOGGER.warning(f"Não foi possível obter credenciais para {user_id}.")
            return None

        required_fields = ['token', 'refresh_token', 'token_uri', 'client_id', 'client_secret', 'scopes']
        if not all(k in stored_data for k in required_fields):
            CALENDAR_UTILS_LOGGER.error(f"Dados de credenciais incompletos para user_id: {user_id}. Campos ausentes: {[k for k in required_fields if k not in stored_data]}")
            return None

        creds = Credentials.from_authorized_user_info(stored_data)

        creds.client_id = self.client_id
        creds.client_secret = self.client_secret
        
        if creds.expired and creds.refresh_token:
            CALENDAR_UTILS_LOGGER.info(f"Credenciais expiradas para {user_id}, tentando refrescar.")
            try:
                await asyncio.to_thread(creds.refresh, Request())
                CALENDAR_UTILS_LOGGER.info(f"Credenciais refrescadas com sucesso para {user_id}.")
                await self._save_credentials(user_id, json.loads(creds.to_json()))
            except RefreshError as e:
                CALENDAR_UTILS_LOGGER.error(f"Erro ao refrescar credenciais para {user_id}: {e}. Credenciais inválidas.", exc_info=True)
                await self.delete_credentials(user_id)
                return None
            except Exception as e:
                CALENDAR_UTILS_LOGGER.error(f"Erro inesperado ao refrescar credenciais para {user_id}: {e}", exc_info=True)
                return None
        elif not creds.valid:
            CALENDAR_UTILS_LOGGER.warning(f"Credenciais inválidas e não refrescáveis para {user_id}. Sugira reautenticação.")
            return None
        
        CALENDAR_UTILS_LOGGER.info(f"Credenciais válidas obtidas para {user_id}.")
        return creds

    async def get_auth_url(self, user_id: str) -> str | None:
        if not self.oauth_config_ready:
            CALENDAR_UTILS_LOGGER.error("Configurações de OAuth não prontas. Não é possível gerar URL de autorização.")
            return None

        CALENDAR_UTILS_LOGGER.debug(f"DEBUG_OAUTH | client_id: {self.client_id}")
        CALENDAR_UTILS_LOGGER.debug(f"DEBUG_OAUTH | client_secret (truncated): {self.client_secret[:5]}...")
        CALENDAR_UTILS_LOGGER.debug(f"DEBUG_OAUTH | redirect_uri: {self.redirect_uri}")
        CALENDAR_UTILS_LOGGER.debug(f"DEBUG_OAUTH | frontend_url (for js_origins): {self.frontend_url}")
        CALENDAR_UTILS_LOGGER.debug(f"DEBUG_OAUTH | GCP_PROJECT: {os.getenv('GCP_PROJECT')}")

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

        unique_state = f"{user_id}-{str(uuid.uuid4())}"

        authorization_url, state_from_flow = await asyncio.to_thread(
            flow.authorization_url,
            access_type='offline',
            include_granted_scopes='true',
            state=unique_state
        )
        
        try:
            doc_ref = await self._get_credentials_doc_ref(user_id)
            # Firestore.DELETE_FIELD needs to be imported or set from firestore module
            from google.cloud import firestore # Temporarily import here if not global
            await asyncio.to_thread(doc_ref.set, {"oauth_state": unique_state}, merge=True)
            CALENDAR_UTILS_LOGGER.info(f"OAuth state '{unique_state}' stored for user '{user_id}'.")
        except Exception as e:
            CALENDAR_UTILS_LOGGER.error(f"Falha ao salvar OAuth state para user '{user_id}': {e}", exc_info=True)
            return None
        
        CALENDAR_UTILS_LOGGER.info(f"URL de autorização gerada para user_id: {user_id}. URL: {authorization_url[:100]}...")
        
        return authorization_url

    async def handle_oauth2_callback(self, authorization_response_url: str) -> dict:
        if not self.oauth_config_ready:
            CALENDAR_UTILS_LOGGER.error("Configurações de OAuth não prontas. Não é possível processar callback.")
            return {"status": "error", "message": "Configuração de OAuth incompleta no backend.", "user_id": None}
        
        from urllib.parse import urlparse, parse_qs
        parsed_url = urlparse(authorization_response_url)
        
        # DEBUG LOG ADICIONAL AQUI
        CALENDAR_UTILS_LOGGER.debug(f"DEBUG_OAUTH_CALLBACK | Protocolo da URL de resposta: {parsed_url.scheme}")
        CALENDAR_UTILS_LOGGER.debug(f"DEBUG_OAUTH_CALLBACK | Host da URL de resposta: {parsed_url.netloc}")
        CALENDAR_UTILS_LOGGER.debug(f"DEBUG_OAUTH_CALLBACK | Caminho da URL de resposta: {parsed_url.path}")
        CALENDAR_UTILS_LOGGER.debug(f"DEBUG_OAUTH_CALLBACK | Query da URL de resposta: {parsed_url.query}")


        query_params = parse_qs(parsed_url.query)
        
        state = query_params.get('state', [None])[0]
        code = query_params.get('code', [None])[0]
        error = query_params.get('error', [None])[0]

        CALENDAR_UTILS_LOGGER.debug(f"DEBUG_OAUTH_CALLBACK | authorization_response_url: {authorization_response_url}")
        CALENDAR_UTILS_LOGGER.debug(f"DEBUG_OAUTH_CALLBACK | state from URL: {state}")
        CALENDAR_UTILS_LOGGER.debug(f"DEBUG_OAUTH_CALLBACK | code from URL: {code}")
        CALENDAR_UTILS_LOGGER.debug(f"DEBUG_OAUTH_CALLBACK | error from URL: {error}")

        if error:
            CALENDAR_UTILS_LOGGER.error(f"OAuth callback recebido com erro: {error}. State: {state}")
            return {"status": "error", "message": f"Autorização Google negada ou falhou: {error}.", "user_id": None}
        
        if not state:
            CALENDAR_UTILS_LOGGER.error("OAuth callback recebido sem 'state'. Possível ataque CSRF ou fluxo inválido.")
            return {"status": "error", "message": "Parâmetro de segurança ausente. Tente novamente.", "user_id": None}
        
        try:
            user_id = state.split('-')[0]
            if not user_id: raise ValueError("User ID not found in state.")
        except Exception as e:
            CALENDAR_UTILS_LOGGER.error(f"Não foi possível extrair user_id do state '{state}': {e}", exc_info=True)
            return {"status": "error", "message": "Erro de segurança: ID de usuário inválido no state.", "user_id": None}

        stored_doc_data = await self._get_stored_credentials(user_id)
        # Import firestore here to use firestore.DELETE_FIELD
        from google.cloud import firestore
        if not stored_doc_data or stored_doc_data.get("oauth_state") != state:
            CALENDAR_UTILS_LOGGER.warning(f"State recebido '{state}' não corresponde ao armazenado para user '{user_id}' ou não encontrado. Potencial CSRF ou reuso.")
            doc_ref = await self._get_credentials_doc_ref(user_id)
            try: await asyncio.to_thread(doc_ref.update, {"oauth_state": firestore.DELETE_FIELD})
            except Exception as e: CALENDAR_UTILS_LOGGER.error(f"Erro ao remover 'oauth_state' para {user_id}: {e}")
            return {"status": "error", "message": "Validação de segurança falhou. Tente conectar novamente.", "user_id": user_id}

        doc_ref = await self._get_credentials_doc_ref(user_id)
        try:
            await asyncio.to_thread(doc_ref.update, {"oauth_state": firestore.DELETE_FIELD})
            CALENDAR_UTILS_LOGGER.info(f"OAuth state '{state}' removido para user '{user_id}' após validação.")
        except Exception as e:
            CALENDAR_UTILS_LOGGER.error(f"Erro ao remover 'oauth_state' para {user_id}: {e}")

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
        flow.redirect_url = self.redirect_uri

        try:
            CALENDAR_UTILS_LOGGER.debug(f"DEBUG_OAUTH_CALLBACK | Calling flow.fetch_token with authorization_response: {authorization_response_url}")
            # Depuração: Logar o redirect_uri que o flow está usando ANTES de fetch_token
            CALENDAR_UTILS_LOGGER.debug(f"DEBUG_OAUTH_CALLBACK | flow.redirect_uri before fetch_token: {flow.redirect_uri}")
            
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
                await self.delete_credentials(user_id)
            return []
        except Exception as e:
            CALENDAR_UTILS_LOGGER.error(f"Erro inesperado ao listar eventos do Google Calendar para {user_id}: {e}", exc_info=True)
            return []

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
# --- END OF FILE google_calendar_utils.py ---