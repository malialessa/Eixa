# Etapa base: Python 3.11 slim
FROM python:3.11-slim

# Define o diretório de trabalho
WORKDIR /app

# Copia os arquivos da aplicação
COPY . .

# Instala as dependências
RUN pip install --no-cache-dir -r requirements.txt

# Expõe a porta padrão do Cloud Run
EXPOSE 8080

# Define a variável de ambiente padrão de porta (boa prática no Cloud Run)
ENV PORT=8080

# Comando de inicialização
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--threads", "25", "--timeout", "300", "main:app"]
