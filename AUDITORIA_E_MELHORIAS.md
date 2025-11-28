# 🔍 Auditoria Completa EIXA - Backend & Frontend

**Data:** 28 de Novembro de 2025  
**Status:** Deploy v2.0 - Correções Críticas Aplicadas ✅

---

## 📊 RESUMO EXECUTIVO

### ✅ Correções Implementadas (Deploy Atual)
1. **Imagens localizadas** - Todas as 7 referências de `storage.googleapis.com/eixa/eixa.svg` → `./assets/img/eixa.svg`
2. **Manifest PWA criado** - `manifest.webmanifest` válido com ícones, shortcuts e metadados
3. **IDs das views corrigidos** - Fix no `switchView()` para converter "agenda" → "agendaViewContent"
4. **Google Calendar funcional** - Handlers completos para conectar/sincronizar/desconectar já implementados

### ⚠️ Problemas Identificados (Pendentes)
- View de Diagnóstico não existe no frontend
- Sistema de sugestões (tarefas/projetos) não está populando UI corretamente
- Upload de arquivos não envia `uploaded_file_data` no formato correto
- Faltam indicadores de loading por view (usando apenas globalLoader)
- Checkpoint semanal forçado sem handler
- Rotinas não têm handlers completos de criação/edição/aplicação

---

## 🎯 AUDITORIA DO BACKEND

### Endpoints Disponíveis

#### 1. **`GET /`** - Health Check
```json
{
  "status": "ok",
  "message": "EIXA está no ar. Use /interact para interagir."
}
```

#### 2. **`GET /auth/google`** - Iniciar OAuth Google Calendar
**Query Params:** `user_id`  
**Retorna:** `{"auth_url": "https://accounts.google.com/o/oauth2/auth?..."}`

#### 3. **`GET /oauth2callback`** - Callback OAuth
**Processa código de autorização e redireciona para:**
- Sucesso: `{FRONTEND_URL}/dashboard?auth_status=success`
- Erro: `{FRONTEND_URL}/dashboard?auth_status=error`

#### 4. **`POST /interact`** - Endpoint Principal
**Request Types Suportados:**

##### a) `request_type: "chat_and_view"` (Padrão)
```javascript
{
  "user_id": "string",
  "request_type": "chat_and_view",
  "message": "string (opcional)",
  "uploaded_file_data": {
    "filename": "string",
    "content_base64": "string",
    "mimetype": "string"
  },
  "view_request": "agenda|projetos|rotinas_templates_view|diagnostico|emotionalMemories|longTermMemory|google_calendar_connection_status"
}
```

**Resposta:**
```javascript
{
  "status": "success|error|info",
  "response": "Mensagem da IA ou feedback",
  "html_view_data": {
    "agenda": [...],           // Lista de tarefas por dia
    "projetos": [...],         // Lista de projetos
    "routines": [...],         // Templates de rotinas
    "emotional_memories": [...],
    "long_term_memory": {...},
    "google_calendar_connected_status": true|false
  },
  "suggested_tasks": [...],    // Tarefas sugeridas pela IA
  "suggested_projects": [...], // Projetos sugeridos pela IA
  "confirmation_required": true|false,
  "confirmation_message": "string"
}
```

##### b) `request_type: "crud_action"`
```javascript
{
  "user_id": "string",
  "request_type": "crud_action",
  "crud_item_type": "task|project|routine",
  "crud_action": "create|update|delete|complete",
  "crud_item_id": "string (para update/delete/complete)",
  "crud_data": {
    // Para task:
    "date": "YYYY-MM-DD",
    "description": "string",
    "time": "HH:MM",
    "duration_minutes": number,
    
    // Para project:
    "nome": "string",
    "descricao": "string",
    "microtarefas": [{"descricao": "string", "concluida": false}],
    
    // Para routine:
    "name": "string",
    "description": "string",
    "schedule": [{"time": "HH:MM", "description": "string", "duration_minutes": number}],
    "recurrence_rule": "daily|weekly|monthly"
  }
}
```

##### c) `request_type: "google_calendar_action"`
```javascript
{
  "user_id": "string",
  "request_type": "google_calendar_action",
  "action": "connect_calendar|sync_calendar|disconnect_calendar",
  "data": {
    "start_date": "YYYY-MM-DD", // Para sync_calendar
    "end_date": "YYYY-MM-DD"    // Para sync_calendar
  }
}
```

---

## 🎨 AUDITORIA DO FRONTEND

### Estrutura de Views

#### Views Implementadas ✅
1. **chatViewContent** - Chat com IA (principal)
2. **agendaViewContent** - Agenda de tarefas do dia
3. **projetosViewContent** - Gerenciamento de projetos
4. **emotionalMemoriesViewContent** - Memórias emocionais
5. **longTermMemoryViewContent** - Perfil e configurações
6. **rotinasTemplatesViewContent** - Templates de rotinas

#### Views Faltantes ❌
- **diagnosticoViewContent** - Backend retorna `view_request='diagnostico'` mas frontend não tem essa view

### Modais Implementados

1. **#editProjectModal** - Editar/criar projetos
2. **#addTaskModal** - Adicionar tarefas manualmente
3. **#editRoutineModal** - Criar/editar rotinas
4. **#confirmationModal** - Confirmações gerais

### Templates HTML

Todos os 13 templates necessários estão presentes:
- ✅ `views-template`
- ✅ `agenda-event-block-template`
- ✅ `project-card-template`
- ✅ `routine-card-template`
- ✅ `microtask-edit-template`
- ✅ `routine-item-edit-template`
- ✅ `diagnostic-card-template`
- ✅ `emotional-memory-card-template`
- ✅ `long-term-profile-template`
- ✅ `editProjectModalTemplate`
- ✅ `addTaskModalTemplate`
- ✅ `editRoutineModalTemplate`
- ✅ `confirmationModalTemplate`

---

## 🔗 MAPEAMENTO BACKEND ↔ FRONTEND

### Google Calendar Integration

#### Backend
```python
# google_calendar_utils.py
- get_auth_url(user_id) → OAuth URL
- handle_oauth2_callback(url) → Processa callback
- get_credentials(user_id) → Verifica se conectado
- delete_credentials(user_id) → Desconecta

# eixa_data.py
- sync_google_calendar_events_to_eixa(user_id, start, end)
```

#### Frontend
```javascript
// Handlers implementados em index.html
- handleConnectGoogleCalendar() → request_type: 'google_calendar_action', action: 'connect_calendar'
- handleSyncGoogleCalendar() → request_type: 'google_calendar_action', action: 'sync_calendar'
- handleDisconnectGoogleCalendar() → request_type: 'google_calendar_action', action: 'disconnect_calendar'
- handleGoogleOAuthCallback() → Processa redirect após OAuth

// Botões na UI
- #connectGoogleCalendarBtn (em longTermMemory)
- #syncGoogleCalendarBtn (em longTermMemory)
- #syncGoogleCalendarBtnAgenda (em agenda)
- #disconnectGoogleCalendarBtn (em longTermMemory)
```

**Status:** ✅ TOTALMENTE FUNCIONAL

---

### CRUD Operations

#### Tarefas (Tasks)

**Backend:** `crud_orchestrator.py`
```python
- _create_task_data(user_id, date_str, description, time_str, duration_minutes)
- _update_task_status_or_data(user_id, date_str, task_id, new_completed_status, new_description, new_time, new_duration_minutes)
- _delete_task_data(user_id, date_str, task_id)
```

**Frontend:** `index.html`
```javascript
// Funções parcialmente implementadas
- saveNewTask() → Chama sendCrudRequest('task', 'create', null, {...})
- Falta: handlers para editar/completar/deletar tarefas diretamente da agenda
```

**Status:** ⚠️ CRIAR funcional, UPDATE/DELETE/COMPLETE precisam de handlers na UI

---

#### Projetos (Projects)

**Backend:** `crud_orchestrator.py`
```python
- _create_project_data(user_id, nome, descricao, microtarefas)
- _update_project_data(user_id, project_id, new_nome, new_descricao, new_microtarefas, microtarefas_to_toggle)
- _delete_project_data(user_id, project_id)
```

**Frontend:** `index.html`
```javascript
// Funções implementadas
- openEditProjectModal(projectData) → Abre modal
- saveProjectChanges() → Chama sendCrudRequest('project', 'create'/'update', ...)
- Falta: handler para deletar projeto (botão existe no modal mas não está conectado)
```

**Status:** ⚠️ CRIAR/EDITAR funcional, DELETE precisa ser implementado

---

#### Rotinas (Routines)

**Backend:** `eixa_data.py`
```python
- save_routine_template(user_id, routine_data)
- apply_routine_to_day(user_id, routine_id, target_date)
- delete_routine_template(user_id, routine_id)
- get_routine_template(user_id, routine_id)
- get_all_routines(user_id)
```

**Frontend:** `index.html`
```javascript
// Funções parcialmente implementadas
- openEditRoutineModal(routineData) → Abre modal
- saveRoutine() → Chama sendCrudRequest('routine', 'create'/'update', ...)
- Falta: handler para aplicar rotina a um dia específico
- Falta: handler para deletar rotina
```

**Status:** ⚠️ CRIAR/EDITAR funcional, APPLY/DELETE não implementados

---

### Sistema de Sugestões

#### Backend
```python
# eixa_orchestrator.py retorna:
{
  "suggested_tasks": [
    {"description": "string", "date": "YYYY-MM-DD", "time": "HH:MM", "duration_minutes": number}
  ],
  "suggested_projects": [
    {"nome": "string", "descricao": "string", "microtarefas": [...]}
  ]
}
```

#### Frontend
```javascript
// Containers existem no HTML
- #suggestedTasksContainer
- #suggestedTasksList
- #suggestedProjectsContainer
- #suggestedProjectsCards

// Função implementada mas não está populando corretamente
- updateSuggestedSections(tasks, projects)

// Falta: Botões de aceitar/rejeitar sugestões
```

**Status:** ⚠️ UI existe mas não está funcional completamente

---

### Upload de Arquivos

#### Backend Espera
```javascript
{
  "uploaded_file_data": {
    "filename": "document.pdf",
    "content_base64": "base64_encoded_content...",
    "mimetype": "application/pdf"
  }
}
```

#### Frontend Atual
```javascript
// Botão existe: #fileUploadBtn
// Input existe: #fileInput
// Falta: Função para ler arquivo, converter para base64 e enviar
```

**Status:** ❌ NÃO FUNCIONAL - Precisa implementar conversão para base64

---

### Checkpoint Semanal

#### Backend
```python
# personal_checkpoint.py
async def run_weekly_checkpoint(user_id):
    """Gera autoavaliação semanal"""
```

#### Frontend
```javascript
// Botão existe mas sem handler
- i18nStrings.forceCheckpointButtonLabel: 'Forçar Checkpoint'
// Falta: Função handleForceCheckpoint()
```

**Status:** ❌ NÃO IMPLEMENTADO

---

## 📋 MELHORIAS SUGERIDAS

### 🔴 Alta Prioridade

#### 1. Implementar View de Diagnóstico
```html
<!-- Adicionar ao views-template -->
<div id="diagnosticoViewContent" class="view-content" role="tabpanel">
    <div class="view-header">
        <h2>Diagnóstico Semanal</h2>
    </div>
    <div id="diagnosticoDisplay" class="diagnostic-display"></div>
</div>
```

```javascript
// Adicionar função
function displayDiagnostico(data) {
    // Renderizar checkpoints e autoavaliações
}
```

#### 2. Completar Upload de Arquivos
```javascript
async function handleFileUpload() {
    const file = elements.fileInput.files[0];
    if (!file) return;
    
    if (file.size > config.MAX_FILE_SIZE) {
        showToast(i18nStrings.fileTooLarge, 'error');
        return;
    }
    
    const reader = new FileReader();
    reader.onload = async (e) => {
        currentAttachment = {
            filename: file.name,
            content_base64: e.target.result.split(',')[1],
            mimetype: file.type
        };
        // Mostrar nome do arquivo na UI
        elements.fileNameDisplay.textContent = file.name;
        elements.attachmentInfoDiv.style.display = 'flex';
    };
    reader.readAsDataURL(file);
}
```

#### 3. Implementar Indicadores de Origem nas Tarefas
```javascript
function renderTaskOriginBadge(task) {
    const origin = task.origin || 'user_added';
    const badges = {
        'user_added': '<span class="badge badge-eixa"><i class="material-icons">person</i> Você</span>',
        'google_calendar': '<span class="badge badge-google"><i class="material-icons">event</i> Google</span>',
        'routine_applied': '<span class="badge badge-routine"><i class="material-icons">repeat</i> Rotina</span>'
    };
    return badges[origin] || '';
}
```

Adicionar CSS:
```css
.badge-eixa {
    background: var(--eixa-task-bg);
    color: var(--eixa-task-color);
}
.badge-google {
    background: var(--google-calendar-event-bg);
    color: var(--google-calendar-event-color);
}
.badge-routine {
    background: var(--routine-task-bg);
    color: var(--routine-task-color);
}
```

#### 4. Completar Handlers de Deletar Tarefa/Projeto/Rotina
```javascript
async function handleDeleteTask(taskId, dateStr) {
    if (!confirm(i18nStrings.confirmTaskDelete + '?')) return;
    
    const result = await sendCrudRequest('task', 'delete', taskId, { date: dateStr });
    if (result?.status === 'success') {
        showToast('Tarefa excluída!', 'success');
        if (result.html_view_data?.agenda) {
            displayAgenda(result.html_view_data.agenda);
        }
    }
}

async function handleDeleteProject(projectId) {
    if (!confirm(i18nStrings.confirmProjectDelete + '?')) return;
    
    const result = await sendCrudRequest('project', 'delete', projectId);
    if (result?.status === 'success') {
        showToast('Projeto excluído!', 'success');
        if (result.html_view_data?.projetos) {
            displayProjects(result.html_view_data.projetos);
        }
    }
}

async function handleDeleteRoutine(routineId) {
    if (!confirm(i18nStrings.confirmRoutineDelete + '?')) return;
    
    const result = await sendCrudRequest('routine', 'delete', routineId);
    if (result?.status === 'success') {
        showToast('Rotina excluída!', 'success');
        loadViewData('rotinasTemplatesView');
    }
}
```

### 🟡 Média Prioridade

#### 5. Sistema de Sugestões Completo
```javascript
function updateSuggestedSections(tasks, projects) {
    // Tarefas sugeridas
    if (tasks && tasks.length > 0) {
        elements.suggestedTasksList.innerHTML = '';
        tasks.forEach((task, index) => {
            const li = document.createElement('li');
            li.innerHTML = `
                <span>${task.description} - ${task.date} às ${task.time}</span>
                <div class="suggestion-actions">
                    <button class="icon-button accept-task-btn" data-task-index="${index}">
                        <i class="material-icons">check</i>
                    </button>
                    <button class="icon-button reject-task-btn" data-task-index="${index}">
                        <i class="material-icons">close</i>
                    </button>
                </div>
            `;
            elements.suggestedTasksList.appendChild(li);
        });
        elements.suggestedTasksContainer.style.display = 'block';
    } else {
        elements.suggestedTasksContainer.style.display = 'none';
    }
    
    // Projetos sugeridos (similar)
}

// Handlers para aceitar/rejeitar
document.addEventListener('click', async (e) => {
    if (e.target.closest('.accept-task-btn')) {
        const index = e.target.closest('.accept-task-btn').dataset.taskIndex;
        const task = currentSuggestedTasks[index];
        await sendCrudRequest('task', 'create', null, {
            date: task.date,
            description: task.description,
            time: task.time,
            duration_minutes: task.duration_minutes
        });
    }
});
```

#### 6. Aplicar Rotina a um Dia
```javascript
async function handleApplyRoutine(routineId, targetDate) {
    const result = await sendCrudRequest('routine', 'apply', routineId, {
        target_date: targetDate
    });
    
    if (result?.status === 'success') {
        showToast(i18nStrings.routineApplySuccess, 'success');
        if (result.html_view_data?.agenda) {
            displayAgenda(result.html_view_data.agenda);
            switchView('agendaViewContent');
        }
    }
}

// Adicionar ao card de rotina:
<button onclick="handleApplyRoutine('${routine.id}', '${new Date().toISOString().split('T')[0]}')">
    <i class="material-icons">play_arrow</i> Aplicar Hoje
</button>
```

#### 7. Checkpoint Semanal Forçado
```javascript
async function handleForceCheckpoint() {
    showGlobalLoader();
    try {
        const payload = {
            request_type: 'chat_and_view',
            message: 'Force checkpoint semanal', // Backend pode ter keyword especial
            // OU criar um endpoint específico /checkpoint
        };
        const response = await callBackendAPI(payload);
        if (response?.status === 'success') {
            showToast('Checkpoint gerado com sucesso!', 'success');
            loadViewData('diagnostico');
        }
    } finally {
        hideGlobalLoader();
    }
}

// Adicionar ao handleGlobalClick
else if (target.closest('#forceCheckpointBtn')) handleForceCheckpoint();
```

### 🟢 Baixa Prioridade

#### 8. Loading States por View
```javascript
function showViewLoading(viewName) {
    const view = document.getElementById(viewName + 'ViewContent');
    if (view) {
        view.innerHTML = `
            <div class="view-loading">
                <img src="./assets/img/eixa.svg" class="loading-spinner" />
                <p>Carregando ${viewName}...</p>
            </div>
        `;
    }
}
```

#### 9. Melhorar Tratamento de Erros de Auth
```javascript
function displayAuthError(message) {
    if (!message || message.trim() === '') {
        message = 'Erro desconhecido na autenticação. Tente novamente.';
    }
    
    if (elements.authErrorMessage) {
        elements.authErrorMessage.textContent = message;
        elements.authErrorMessage.style.display = 'block';
    }
    
    console.error("DEBUG: Auth Error:", message);
}
```

#### 10. Temas e Acessibilidade
- Adicionar mais variações de tema (ex: tema "foco" com menos distrações)
- Melhorar contraste de cores para WCAG AAA
- Adicionar atalhos de teclado para ações comuns
- Implementar navegação por teclado nos modais

---

## 🚀 ROADMAP DE IMPLEMENTAÇÃO

### Sprint 1 (Semana 1-2) - Funcionalidades Core
- [ ] View de Diagnóstico
- [ ] Upload de arquivos funcional
- [ ] Handlers de delete (tarefa/projeto/rotina)
- [ ] Indicadores de origem das tarefas

### Sprint 2 (Semana 3-4) - Sistema de Sugestões
- [ ] Completar UI de sugestões
- [ ] Botões de aceitar/rejeitar
- [ ] Aplicar rotina a um dia
- [ ] Checkpoint forçado

### Sprint 3 (Semana 5-6) - UX e Polish
- [ ] Loading states por view
- [ ] Melhorias de erro handling
- [ ] Atalhos de teclado
- [ ] Animações e transições

### Sprint 4 (Semana 7-8) - Features Avançadas
- [ ] Notificações push (PWA)
- [ ] Modo offline (cache)
- [ ] Exportar dados
- [ ] Integrações adicionais

---

## 📊 MÉTRICAS DE QUALIDADE

### Backend
- ✅ Logging estruturado em JSON
- ✅ Tratamento de erros com try/catch
- ✅ Validação de inputs
- ✅ Separação de concerns (orchestrator pattern)
- ✅ Async/await consistente
- ⚠️ Faltam testes unitários

### Frontend
- ✅ Estrutura modular com funções separadas
- ✅ Acessibilidade (ARIA labels, roles)
- ✅ Responsividade (mobile-first)
- ✅ Dark mode implementado
- ✅ i18n preparado (strings centralizadas)
- ⚠️ Faltam testes E2E
- ⚠️ Performance não otimizada (muitas chamadas ao backend)

---

## 🔧 CONFIGURAÇÃO NECESSÁRIA

### Variáveis de Ambiente no Cloud Run

Verificar se estão configuradas:
```bash
gcloud run services describe eixa-api --region us-east1 --format="value(spec.template.spec.containers[0].env)"
```

Necessárias:
- `GCP_PROJECT=arquitetodadivulgacao`
- `REGION=us-east1`
- `GEMINI_API_KEY=AIza...`
- `GOOGLE_CLIENT_ID=760851989407-...`
- `GOOGLE_CLIENT_SECRET=GOCSPX-...`
- `GOOGLE_REDIRECT_URI=https://eixa-760851989407.us-east1.run.app/oauth2callback`
- `FRONTEND_URL=https://eixa.web.app`
- `FIRESTORE_DATABASE_ID=eixa`

---

## 📞 SUPORTE E CONTATO

- **Deploy URL:** https://eixa.web.app
- **Backend URL:** https://eixa-760851989407.us-east1.run.app
- **Firebase Console:** https://console.firebase.google.com/project/arquitetodadivulgacao
- **GCP Console:** https://console.cloud.google.com/run?project=arquitetodadivulgacao

---

**Última Atualização:** 28/11/2025 - Deploy v2.0
