# app_config_loader.py
import os
import yaml
import logging

logger = logging.getLogger(__name__)

_config_cache = {}

def load_yaml_config(filepath: str, default_value: dict = None, log_name: str = "config"):
    """
    Carrega um arquivo YAML, com cache para evitar múltiplas leituras.
    """
    if filepath in _config_cache:
        return _config_cache[filepath]

    if default_value is None:
        default_value = {}
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        full_filepath = os.path.join(current_dir, filepath)
        with open(full_filepath, 'r', encoding='utf-8') as f:
            config_data = yaml.safe_load(f)
        logger.info(f"{log_name.capitalize()} loaded successfully from {full_filepath}.")
        
        # Armazena em cache antes de retornar
        _config_cache[filepath] = config_data if config_data is not None else default_value
        return _config_cache[filepath]
    except FileNotFoundError:
        logger.warning(f"{filepath} not found. Running without a {log_name}.", exc_info=True)
        return default_value
    except yaml.YAMLError as e:
        logger.warning(f"Error loading {filepath}. Running without a {log_name}. Error: {e}", exc_info=True)
        return default_value

def get_eixa_templates():
    """
    Retorna todos os templates YAML carregados e cacheados.
    Carrega-os se ainda não estiverem no cache.
    """
    if "eixa_persona_template_text" not in _config_cache:
        _prompt_config_data = load_yaml_config('prompt_config.yaml', {}, "prompt config")
        _config_cache["eixa_persona_template_text"] = _prompt_config_data.get('base_eixa_persona', '')

        _user_profile_template_full = load_yaml_config('user_profile_template.yaml', {}, "user profile template")
        _config_cache["user_profile_template_content"] = _user_profile_template_full.get('user_profile', {})

        _user_flags_template_full = load_yaml_config('user_flags.yaml', {}, "user flags template")
        _config_cache["user_flags_template_content"] = _user_flags_template_full.get('behavior_flags', {})
    
    return (
        _config_cache["eixa_persona_template_text"],
        _config_cache["user_profile_template_content"],
        _config_cache["user_flags_template_content"]
    )