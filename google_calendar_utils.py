import asyncio
import os
import logging
from datetime import datetime, timedelta

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from firestore_client_singleton import FirestoreClientSingleton # Assumindo que este é o caminho correto

# Configuração de logging
logging.basicConfig(level=logging.INFO)
CALENDAR_UTILS_LOGGER = logging.getLogger("CALENDAR_UTILS")

# Scopes necessários para acessar o Google Calendar.
# Calendar.readonly para apenas ler eventos.
# Calendar para ler e escrever (se você planeja two-way sync no futuro).
# Para sincronização bidirecional, você precisará: 'https://www.googleapis.com/auth/calendar'
GOOGLE_CALENDAR_SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

# Coleção no Firestore para armazenar tokens de autenticação do Google
GOOGLE_AUTH_COLLECTION = "eixa_google_auth"

class GoogleCalendarUtils:
    def __init__(self):
        self.db = FirestoreClientSingleton.get_instance()

    async def _get_credentials_doc_ref(self, user_id: str):
        return self.db.collection(GOOGLE_AUTH_COLLECTION).document(user_id)

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

    async def get_credentials(self, user_id: str) -> Credentials | None:
        """
        Obtém credenciais do Firestore e as refresca se necessário.
        Retorna um objeto Credentials ou None se não houver credenciais válidas.
        """
        CALENDAR_UTILS_LOGGER.info(f"Obtendo e refrescando credenciais para user_id: {user_id}")
        stored_data = await self._get_stored_credentials(user_id)
        if not stored_data:
            CALENDAR_UTILS_LOGGER.warning(f"Não foi possível obter credenciais para {user_id}.")
            return None

        # Certifique-se de que os campos necessários para Credentials.from_authorized_user_info estão presentes
        # (token, refresh_token, token_uri, client_id, client_secret, scopes)
        if not all(k in stored_data for k in ['token', 'refresh_token', 'token_uri', 'client_id', 'client_secret', 'scopes']):
            CALENDAR_UTILS_LOGGER.error(f"Dados de credenciais incompletos para user_id: {user_id}. Dados: {stored_data.keys()}")
            return None

        creds = Credentials.from_authorized_user_info(stored_data)

        if creds.expired and creds.refresh_token:
            CALENDAR_UTILS_LOGGER.info(f"Credenciais expiradas para {user_id}, tentando refrescar.")
            try:
                # O Request precisa ser feito em uma thread separada para não bloquear o loop de eventos
                await asyncio.to_thread(creds.refresh, Request())
                CALENDAR_UTILS_LOGGER.info(f"Credenciais refrescadas com sucesso para {user_id}.")
                # Salva as credenciais refrescadas de volta no Firestore
                await self._save_credentials(user_id, creds.to_json())
            except Exception as e:
                CALENDAR_UTILS_LOGGER.error(f"Erro ao refrescar credenciais para {user_id}: {e}", exc_info=True)
                # Se falhou ao refrescar, as credenciais não são mais válidas
                return None
        elif not creds.valid:
            CALENDAR_UTILS_LOGGER.warning(f"Credenciais inválidas e sem refresh_token para {user_id}.")
            return None
        
        CALENDAR_UTILS_LOGGER.info(f"Credenciais válidas obtidas para {user_id}.")
        return creds

    async def list_calendar_events(self, user_id: str, time_min: datetime, time_max: datetime) -> list:
        """
        Lista eventos do Google Calendar para o usuário e período especificados.
        Retorna uma lista de eventos brutos da API.
        """
        creds = await self.get_credentials(user_id)
        if not creds:
            CALENDAR_UTILS_LOGGER.warning(f"Não há credenciais válidas para listar eventos para user_id: {user_id}")
            return []

        try:
            service = await asyncio.to_thread(build, 'calendar', 'v3', credentials=creds)

            # Chamada à API para listar eventos
            CALENDAR_UTILS_LOGGER.info(f"Listando eventos do Google Calendar para user_id: {user_id} de {time_min} a {time_max}")
            
            # --- CORREÇÃO AQUI ---
            events_result = await asyncio.to_thread(
                lambda: service.events().list(
                    calendarId='primary', # 'primary' refere-se ao calendário principal do usuário
                    timeMin=time_min.isoformat() + 'Z',
                    timeMax=time_max.isoformat() + 'Z',
                    maxResults=100, # Limite de resultados por requisição
                    singleEvents=True,
                    orderBy='startTime'
                ).execute()
            )
            # --- FIM DA CORREÇÃO ---

            events = events_result.get('items', [])
            CALENDAR_UTILS_LOGGER.info(f"Encontrados {len(events)} eventos do Google Calendar para user_id: {user_id}")
            return events

        except HttpError as error:
            CALENDAR_UTILS_LOGGER.error(f"Erro ao listar eventos do Google Calendar para {user_id}: {error.content}", exc_info=True)
            return []
        except Exception as e:
            CALENDAR_UTILS_LOGGER.error(f"Erro inesperado ao listar eventos do Google Calendar para {user_id}: {e}", exc_info=True)
            return []

    # --- Funções para Sincronização Bidirecional (Futuro) ---
    # Estes são placeholders para quando você quiser que a EIXA crie/atualize eventos no Google Calendar

    async def create_calendar_event(self, user_id: str, event_data: dict) -> dict | None:
        """Cria um evento no Google Calendar."""
        CALENDAR_UTILS_LOGGER.info(f"Tentando criar evento no Google Calendar para user_id: {user_id}")
        creds = await self.get_credentials(user_id)
        if not creds:
            CALENDAR_UTILS_LOGGER.warning(f"Não há credenciais válidas para criar evento para user_id: {user_id}")
            return None
        try:
            service = await asyncio.to_thread(build, 'calendar', 'v3', credentials=creds)
            # --- CORREÇÃO AQUI ---
            event = await asyncio.to_thread(
                lambda: service.events().insert(calendarId='primary', body=event_data).execute()
            )
            # --- FIM DA CORREÇÃO ---
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
            # --- CORREÇÃO AQUI ---
            event = await asyncio.to_thread(
                lambda: service.events().update(calendarId='primary', eventId=event_id, body=event_data).execute()
            )
            # --- FIM DA CORREÇÃO ---
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
            # --- CORREÇÃO AQUI ---
            await asyncio.to_thread(
                lambda: service.events().delete(calendarId='primary', eventId=event_id).execute()
            )
            # --- FIM DA CORREÇÃO ---
            CALENDAR_UTILS_LOGGER.info(f"Evento {event_id} deletado do Google Calendar.")
            return True
        except HttpError as error:
            CALENDAR_UTILS_LOGGER.error(f"Erro ao deletar evento {event_id} do Google Calendar para {user_id}: {error.content}", exc_info=True)
            return False
        except Exception as e:
            CALENDAR_UTILS_LOGGER.error(f"Erro inesperado ao deletar evento {event_id} do Google Calendar para {user_id}: {e}", exc_info=True)
            return False