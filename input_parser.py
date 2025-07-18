# input_parser.py
import base64
import logging
# Reutiliza a lógica de processamento de arquivos do file_utils
import file_utils  # ALTERADO: Importa o módulo inteiro
from typing import Dict

logger = logging.getLogger(__name__)

def parse_incoming_input(
    user_message: str = None, 
    uploaded_file_data: dict = None
) -> dict:
    """
    Normaliza e processa a entrada do usuário, que pode ser texto ou multimodal.
    Retorna um dicionário com:
    - 'input_type': 'text' ou 'multimodal'
    - 'user_input_for_saving': String para salvar (mensagem ou descrição do arquivo)
    - 'prompt_parts_for_gemini': Lista de partes (texto/imagem) para o prompt do Gemini
    - 'gemini_model_override': 'gemini-pro-vision' se for imagem, senão None
    """
    
    processed_file_content = None
    prompt_parts_for_gemini = []
    gemini_model_override = None
    input_type = 'text'
    user_input_for_saving = user_message  # Default para salvar, será sobrescrito se houver arquivo

    if uploaded_file_data:
        input_type = 'multimodal'
        try:
            # ALTERADO: Acessa a função via o módulo importado
            processed_file_content = file_utils.process_uploaded_file( 
                uploaded_file_data['base64'], 
                uploaded_file_data['filename'], 
                uploaded_file_data['mimetype']
            )
            logger.info(f"Arquivo '{uploaded_file_data.get('filename')}' processado com sucesso. Tipo: {processed_file_content['type']}.")

            if processed_file_content['type'] == 'image':
                prompt_parts_for_gemini.append({
                    "inlineData": {
                        "mimeType": processed_file_content['metadata']['mime_type'],
                        "data": processed_file_content['content']['base64_image']
                    }
                })
                gemini_model_override = 'gemini-pro-vision'
                # Define a string a ser salva no histórico de interação
                user_input_for_saving = f"[IMAGEM: {uploaded_file_data['filename']}] {user_message or ''}".strip()

            elif processed_file_content['type'] == 'text':  # PDF ou DOCX
                prompt_parts_for_gemini.append({
                    "text": f"Conteúdo do arquivo '{uploaded_file_data['filename']}':\n{processed_file_content['content']['text_content']}\n\n"
                })
                # Define a string a ser salva no histórico de interação
                user_input_for_saving = f"[DOC: {uploaded_file_data['filename']}] {user_message or ''}".strip()

            # Se houver uma mensagem de texto junto com o arquivo, adiciona-a como parte separada do prompt
            if user_message:
                prompt_parts_for_gemini.append({"text": user_message}) # Removi "Usuário diz:" para ser mais natural para o LLM

        except ValueError as ve:
            logger.error(f"Erro ao processar arquivo enviado: {ve}", exc_info=True)
            # Re-raise para que o eixa_orchestrator possa capturar e retornar um erro ao frontend
            raise  
        except Exception as e:
            logger.error(f"Erro inesperado durante o processamento do arquivo: {e}", exc_info=True)
            # Re-raise para que o eixa_orchestrator possa capturar e retornar um erro ao frontend
            raise  

    # Se não houver arquivo e houver mensagem, a entrada é puramente textual
    if not processed_file_content and user_message:
        prompt_parts_for_gemini.append({"text": user_message})
        
    return {
        'input_type': input_type,
        'user_input_for_saving': user_input_for_saving,
        'prompt_parts_for_gemini': prompt_parts_for_gemini,
        'gemini_model_override': gemini_model_override
    }