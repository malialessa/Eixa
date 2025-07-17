import logging
import re
from typing import Dict, Any
import copy
from datetime import datetime, timezone

# Importa as funções de utilidade do Firestore
from firestore_utils import get_user_profile_data, set_firestore_document

logger = logging.getLogger(__name__)

# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
# REMOVA ESTE BLOCO DE IMPORTAÇÃO QUE CAUSA A CIRCULARIDADE
# from eixa_orchestrator import _user_profile_template_content as minimal_profile_template_content
# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<

async def parse_and_update_profile_settings(user_id: str, message: str, user_profile_template: Dict[str, Any]) -> Dict[str, Any]:
    """
    Analisa a mensagem do usuário para detectar intenções de alterar configurações do perfil
    e atualiza o perfil no Firestore.
    Recebe user_profile_template para ser usado por get_user_profile_data se o perfil não existir.
    Retorna uma mensagem de ação e um booleano indicando se o perfil foi atualizado.
    """
    action_message = ""
    profile_updated = False

    lower_message = message.lower()

    # --- Lógica para ativar/desativar a exibição do perfil na aba de memória de longo prazo ---
    if re.search(r'(mostrar|exibir|ativar)\s+(meu perfil completo|minha memória de longo prazo|meu perfil pela eixa|o que você sabe sobre mim)', lower_message):
        # Passa user_profile_template para get_user_profile_data
        user_profile = await get_user_profile_data(user_id, user_profile_template)
        if user_profile:
            current_setting = user_profile.get('eixa_interaction_preferences', {}).get('display_profile_in_long_term_memory', False)
            if not current_setting:
                user_profile.setdefault('eixa_interaction_preferences', {})['display_profile_in_long_term_memory'] = True
                await set_firestore_document('profiles', user_id, {'user_profile': user_profile}, merge=True)
                action_message = "Entendido. Seu perfil completo será exibido na aba de 'Memória de Longo Prazo'."
                profile_updated = True
            else:
                action_message = "A exibição do seu perfil já estava ativada."
        else:
            action_message = "Não foi possível atualizar seu perfil. Tente novamente."
    elif re.search(r'(ocultar|não mostrar|desativar)\s+(meu perfil completo|minha memória de longo prazo|meu perfil pela eixa|o que você sabe sobre mim)', lower_message):
        user_profile = await get_user_profile_data(user_id, user_profile_template) # Passa template
        if user_profile:
            current_setting = user_profile.get('eixa_interaction_preferences', {}).get('display_profile_in_long_term_memory', True)
            if current_setting:
                user_profile.setdefault('eixa_interaction_preferences', {})['display_profile_in_long_term_memory'] = False
                await set_firestore_document('profiles', user_id, {'user_profile': user_profile}, merge=True)
                action_message = "Certo. Seu perfil completo não será mais exibido na aba de 'Memória de Longo Prazo'."
                profile_updated = True
            else:
                action_message = "A exibição do seu perfil já estava desativada."
        else:
            action_message = "Não foi possível atualizar seu perfil. Tente novamente."

    elif re.search(r'(parar de mandar|não mandar mais|desativar|ocultar)\s+(memórias emocionais|memórias recentes|esse resumo de memórias|as memórias)', lower_message):
        user_profile = await get_user_profile_data(user_id, user_profile_template) # Passa template
        if user_profile:
            current_setting = user_profile.get('eixa_interaction_preferences', {}).get('display_emotional_memories', True)
            if current_setting:
                user_profile.setdefault('eixa_interaction_preferences', {})['display_emotional_memories'] = False
                await set_firestore_document('profiles', user_id, {'user_profile': user_profile}, merge=True)
                action_message = "Entendido. Não exibirei mais as 'Memórias emocionais recentes' nas próximas interações."
                profile_updated = True
            else:
                action_message = "As 'Memórias emocionais recentes' já estavam desativadas."
        else:
            action_message = "Não foi possível atualizar seu perfil. Tente novamente."
    elif re.search(r'(voltar a mandar|exibir|ativar|mostrar)\s+(memórias emocionais|memórias recentes|o resumo de memórias)', lower_message):
        user_profile = await get_user_profile_data(user_id, user_profile_template) # Passa template
        if user_profile:
            current_setting = user_profile.get('eixa_interaction_preferences', {}).get('display_emotional_memories', False)
            if not current_setting:
                user_profile.setdefault('eixa_interaction_preferences', {})['display_emotional_memories'] = True
                await set_firestore_document('profiles', user_id, {'user_profile': user_profile}, merge=True)
                action_message = "Certo. Voltarei a exibir as 'Memórias emocionais recentes'."
                profile_updated = True
            else:
                action_message = "As 'Memórias emocionais recentes' já estavam ativadas."
        else:
            action_message = "Não foi possível atualizar seu perfil. Tente novamente."

    return {"action_message": action_message, "profile_updated": profile_updated}


# NOVA FUNÇÃO AUXILIAR: Normaliza a estrutura de goals
def _normalize_goals_structure(goals_data: dict) -> dict:
    """
    Normaliza a estrutura da seção 'goals' para garantir que sejam listas de dicionários
    com a chave 'value'.
    """
    normalized_goals = {}
    for term_type in ['short_term', 'medium_term', 'long_term']:
        items = goals_data.get(term_type, [])
        if isinstance(items, list):
            normalized_items = []
            for item in items:
                if isinstance(item, str):
                    normalized_items.append({"value": item})
                elif isinstance(item, dict) and "value" in item:
                    normalized_items.append(item)
                else:
                    # Se for um dicionário mas não tiver 'value', tenta usar o primeiro valor
                    if isinstance(item, dict) and item:
                        first_value = next(iter(item.values()), None) # Pega o primeiro valor do dict
                        if first_value:
                            normalized_items.append({"value": str(first_value)})
                        else:
                            logger.warning(f"Unexpected empty dict in goal item for {term_type}: {item}. Skipping.")
                    else:
                        logger.warning(f"Unexpected goal item format for {term_type}: {item}. Skipping.")
            normalized_goals[term_type] = normalized_items
        else:
            logger.warning(f"Goals '{term_type}' is not a list. Skipping normalization.")
            normalized_goals[term_type] = []
    return normalized_goals

async def update_profile_from_inferred_data(user_id: str, inferred_profile_data: Dict[str, Any], user_profile_template: Dict[str, Any]):
    """
    Atualiza o perfil do usuário no Firestore com base em dados inferidos pelo LLM.
    Recebe user_profile_template para ser usado por get_user_profile_data se o perfil não existir.
    Usa merge cuidadoso para atualizar apenas os campos fornecidos.
    """
    if not inferred_profile_data:
        logger.debug(f"No inferred profile data provided for user '{user_id}'. Skipping update.")
        return

    # Passa user_profile_template para get_user_profile_data
    current_profile = await get_user_profile_data(user_id, user_profile_template)

    if not current_profile:
        logger.error(f"Could not retrieve or create base profile for user '{user_id}'. Cannot apply inferred data.")
        return

    updated_profile = copy.deepcopy(current_profile)

    # Função auxiliar para mesclar listas, adicionando itens únicos
    def merge_unique_list_items(target_list: list, new_items: list, key_for_uniqueness: str = None):
        if not isinstance(target_list, list):
            logger.warning(f"Target is not a list. Converting or replacing. Target: {target_list}, New: {new_items}")
            return list(new_items) # Ou [] se preferir um reset se o tipo for inesperado
        
        existing_keys = set()
        if key_for_uniqueness:
            for item in target_list:
                if isinstance(item, dict) and key_for_uniqueness in item:
                    existing_keys.add(item[key_for_uniqueness])
                elif isinstance(item, str): # Trata o caso de lista de strings
                    existing_keys.add(item)
        else: # Se não houver chave de unicidade, verifica o item inteiro
            existing_keys = set(item for item in target_list if isinstance(item, (str, int, float, bool))) # Apenas tipos hashable

        for new_item in new_items:
            if key_for_uniqueness and isinstance(new_item, dict) and key_for_uniqueness in new_item:
                if new_item[key_for_uniqueness] not in existing_keys:
                    target_list.append(new_item)
                    existing_keys.add(new_item[key_for_uniqueness])
            elif not key_for_uniqueness and isinstance(new_item, (str, int, float, bool)):
                if new_item not in existing_keys:
                    target_list.append(new_item)
                    existing_keys.add(new_item)
            elif not key_for_uniqueness and isinstance(new_item, dict):
                # Para dicionários sem chave de unicidade, apenas adiciona se não for idêntico
                if new_item not in target_list: # Comparação de dicionários completa
                    target_list.append(new_item)
            else:
                logger.warning(f"Skipping merge of complex item without uniqueness key or unexpected type: {new_item}")
        return target_list

    # Itera sobre as seções do perfil que o LLM pode inferir
    for section_name, section_data in inferred_profile_data.items():
        if section_name == "name":
            # Atualiza o nome diretamente, se fornecido
            if section_data:
                updated_profile['name'] = section_data
                logger.debug(f"Updating user name to: {section_data}")
            continue

        # Para seções que são dicionários aninhados (ex: psychological_profile, communication_preferences)
        if isinstance(section_data, dict):
            target_section = updated_profile.setdefault(section_name, {})
            if not isinstance(target_section, dict): # Caso o tipo esteja incorreto no perfil existente
                logger.warning(f"Profile section '{section_name}' in current profile is not a dict. Overwriting with inferred dict.")
                target_section = {} # Reseta para dict vazio antes de mesclar

            for key, value in section_data.items():
                if isinstance(value, list):
                    # Lógica para listas: mesclar itens únicos
                    if key == "goals": # Trata goals separadamente devido à estrutura de dicionários
                        for term_type in ['long_term', 'medium_term', 'short_term']:
                            if term_type in value and isinstance(value[term_type], list):
                                current_goals_list = target_section.setdefault(term_type, [])
                                if not isinstance(current_goals_list, list): current_goals_list = [] # Garante tipo correto
                                merge_unique_list_items(current_goals_list, value[term_type], 'value')
                    elif key == "supplements": # Trata supplements como lista de dicts com 'name'
                         current_list = target_section.setdefault(key, [])
                         if not isinstance(current_list, list):
                             logger.warning(f"Supplements in current profile is not a list. Overwriting.")
                             current_list = []
                         merge_unique_list_items(current_list, value, 'name')
                    elif key == "current_projects": # Trata projects como lista de dicts com 'name'
                         current_list = target_section.setdefault(key, [])
                         if not isinstance(current_list, list):
                             logger.warning(f"Current projects in current profile is not a list. Overwriting.")
                             current_list = []
                         merge_unique_list_items(current_list, value, 'name')
                    else: # Outras listas (traits, patterns, cognitive_style, archetypes, etc.)
                        current_list = target_section.setdefault(key, [])
                        if not isinstance(current_list, list):
                            logger.warning(f"Profile sub-section list '{key}' in current profile is not a list. Overwriting.")
                            current_list = []
                        merge_unique_list_items(current_list, value)
                else:
                    # Para outros valores (strings, booleanos, números), apenas atualiza
                    target_section[key] = value

        # Para seções que são listas no nível superior (ex: data_usage_consent, specific_no_gos se LLM enviar assim)
        elif isinstance(section_data, list):
            current_list = updated_profile.setdefault(section_name, [])
            if not isinstance(current_list, list): # Caso o tipo esteja incorreto no perfil existente
                logger.warning(f"Profile section '{section_name}' in current profile is not a list. Overwriting.")
                current_list = []
            merge_unique_list_items(current_list, section_data)
        else:
            # Para outros tipos de dados de nível superior que o LLM possa inferir diretamente (ex: locale, timezone)
            updated_profile[section_name] = section_data


    # Garante que as datas de atualização sejam mantidas
    updated_profile['last_updated'] = datetime.now(timezone.utc).isoformat()

    try:
        # Salva o perfil atualizado. Use merge=False para sobrescrever o documento 'user_profile'
        # para garantir que a estrutura complexa (listas, dicts aninhados) seja salva corretamente,
        # e a lógica de merge de conteúdo é feita no código Python.
        # Ou merge=True se você quer que o Firestore mescle no nível mais alto e confiar no Firestore
        # para merges de dicionários, mas listas ainda precisarão de lógica Python.
        # Considerando a complexidade do merge acima, é mais seguro substituir a sub-chave 'user_profile'
        # para garantir que todas as modificações da lógica de merge sejam aplicadas.
        await set_firestore_document('profiles', user_id, {'user_profile': updated_profile}, merge=False)
        logger.info(f"User profile for '{user_id}' updated with inferred data from LLM.")
    except Exception as e:
        logger.error(f"Failed to save inferred profile data for user '{user_id}': {e}", exc_info=True)