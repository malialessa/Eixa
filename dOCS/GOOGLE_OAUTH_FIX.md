# 🔧 Guia: Corrigir OAuth Google Calendar - "App não verificado"

## ⚠️ Problema
Ao tentar conectar o Google Calendar, aparece o aviso "Este app não foi verificado" e a conexão não finaliza.

## 🎯 Causa
O Google bloqueia apps não verificados que solicitam acesso a dados sensíveis (como Google Calendar). Para desenvolvimento e uso pessoal, podemos configurar o app como "Teste" ou verificá-lo oficialmente.

---

## 🚀 SOLUÇÃO RÁPIDA (Modo Teste - Recomendado para Desenvolvimento)

### Passo 1: Acessar Google Cloud Console
```bash
https://console.cloud.google.com/apis/credentials?project=arquitetodadivulgacao
```

### Passo 2: Configurar OAuth Consent Screen

1. No menu lateral, clique em **"OAuth consent screen"**

2. Configure os seguintes campos:
   - **User Type**: Escolha **"External"** (permite qualquer conta Google)
   - Clique em **"EDIT APP"** ou **"CRIAR"**

3. Preencha as informações:
   ```
   Nome do app: EIXA
   E-mail de suporte do usuário: seu-email@gmail.com
   Logo do app: (opcional) Upload do eixa.svg
   Domínio do app: eixa.web.app
   Link da política de privacidade: https://eixa.web.app/privacy (criar depois)
   Link dos termos de serviço: https://eixa.web.app/terms (criar depois)
   ```

4. **IMPORTANTE**: Na seção **"Test users"**, adicione:
   - Seu e-mail pessoal
   - Qualquer outro e-mail que você queira testar
   - ⚠️ **Apenas contas listadas aqui poderão usar o app em modo teste**

5. Em **"Scopes"**, adicione:
   ```
   https://www.googleapis.com/auth/calendar.readonly
   https://www.googleapis.com/auth/calendar.events
   ```

### Passo 3: Configurar Redirect URIs

1. Vá para **"Credentials"** > Clique no OAuth Client criado

2. Em **"Authorized redirect URIs"**, adicione:
   ```
   https://eixa-760851989407.us-east1.run.app/oauth2callback
   https://eixa.web.app/oauth2callback
   http://localhost:8080/oauth2callback (para testes locais)
   ```

3. Em **"Authorized JavaScript origins"**, adicione:
   ```
   https://eixa.web.app
   https://eixa-760851989407.us-east1.run.app
   http://localhost:8080
   ```

4. Clique em **"SALVAR"**

### Passo 4: Verificar Variáveis de Ambiente no Cloud Run

```bash
# Verificar configuração atual
gcloud run services describe eixa-api --region us-east1 --format="value(spec.template.spec.containers[0].env)"

# Se necessário, atualizar
gcloud run services update eixa-api --region us-east1 \
  --set-env-vars GOOGLE_REDIRECT_URI=https://eixa-760851989407.us-east1.run.app/oauth2callback,FRONTEND_URL=https://eixa.web.app
```

### Passo 5: Testar o Fluxo

1. Acesse https://eixa.web.app
2. Faça login
3. Vá para o Perfil (ícone de pessoa)
4. Clique em "Conectar Google Calendar"
5. **No aviso "App não verificado"**, clique em:
   - "Avançado" (canto inferior esquerdo)
   - "Ir para EIXA (não seguro)"
6. Autorize o acesso
7. Você será redirecionado de volta e verá "Google Calendar conectado com sucesso!"

---

## 🏆 SOLUÇÃO PERMANENTE (Verificação Oficial - Para Produção)

### Quando usar:
- Quando você quiser que **qualquer pessoa** use o app sem restrições
- Quando estiver pronto para lançar publicamente

### Processo:
1. Complete a tela de consentimento OAuth
2. Prepare documentação:
   - Política de privacidade
   - Termos de serviço
   - Vídeo demonstrando o uso dos escopos do Google Calendar
3. Submeta para revisão: https://console.cloud.google.com/apis/credentials/consent
4. Aguarde 4-6 semanas para aprovação

---

## 🔍 DIAGNÓSTICO DE PROBLEMAS

### Erro: "redirect_uri_mismatch"
**Causa**: URI de redirecionamento não autorizado

**Solução**:
```bash
# Ver URIs configurados
gcloud auth application-default login
gcloud projects describe arquitetodadivulgacao

# O redirect_uri DEVE ser EXATAMENTE:
https://eixa-760851989407.us-east1.run.app/oauth2callback
```

### Erro: "Access blocked: This app's request is invalid"
**Causa**: Scopes não configurados ou app em status Draft

**Solução**:
1. Vá para OAuth consent screen
2. Certifique-se de que o status é **"Testing"** ou **"In production"**
3. Adicione os scopes necessários
4. Adicione seu e-mail em "Test users"

### Erro: "User is not added to test users"
**Causa**: Você não está na lista de usuários de teste

**Solução**:
1. OAuth consent screen > Test users > Add users
2. Adicione o e-mail que você está usando para login

---

## ✅ CHECKLIST DE VERIFICAÇÃO

Antes de testar, certifique-se de que:

- [ ] OAuth consent screen está configurado (External)
- [ ] Scopes do Calendar estão adicionados
- [ ] Seu e-mail está em "Test users"
- [ ] Redirect URIs estão corretos no OAuth Client
- [ ] JavaScript origins estão adicionados
- [ ] Variáveis de ambiente do Cloud Run estão corretas
- [ ] Deploy do backend foi feito (se mudou URIs)

---

## 📋 COMANDOS ÚTEIS

```bash
# Ver configuração OAuth
gcloud auth application-default print-access-token

# Ver logs do Cloud Run
gcloud run services logs read eixa-api --region us-east1 --limit 50

# Testar endpoint OAuth callback
curl -I https://eixa-760851989407.us-east1.run.app/oauth2callback

# Ver env vars
gcloud run services describe eixa-api --region us-east1 --format="value(spec.template.spec.containers[0].env)"
```

---

## 🎯 STATUS ATUAL DO SEU PROJETO

### OAuth Client ID
```
760851989407-clu976to4jbf4j73udp3rol516uer0sb.apps.googleusercontent.com
```

### URLs Configuradas
- Backend: `https://eixa-760851989407.us-east1.run.app`
- Frontend: `https://eixa.web.app`
- Callback: `https://eixa-760851989407.us-east1.run.app/oauth2callback`

### Próximos Passos
1. ✅ Adicione seu e-mail aos Test Users
2. ✅ Configure os Redirect URIs
3. ✅ Teste o fluxo OAuth
4. ⏳ (Opcional) Submeta para verificação oficial quando estiver pronto para produção

---

**Última Atualização:** 28/11/2025
