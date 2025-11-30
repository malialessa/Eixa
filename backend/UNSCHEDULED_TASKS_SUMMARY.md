# Implementação de Tarefas Não Agendadas - Sumário

## Status: Backend Completo ✅

### Arquivos Modificados

1. **backend/config.py** ✅
   - Adicionado mapeamento `'unscheduled': 'unscheduled_tasks'`

2. **backend/collections_manager.py** ✅
   - Funções novas:
     - `get_unscheduled_tasks_collection(user_id)`
     - `get_unscheduled_task_doc_ref(user_id, task_id)`

3. **backend/eixa_data.py** ✅
   - Funções novas:
     - `get_all_unscheduled_tasks(user_id)` - Lista todas as tarefas não agendadas
     - `save_unscheduled_task(user_id, task_id, data)` - Salva tarefa não agendada
     - `delete_unscheduled_task(user_id, task_id)` - Remove tarefa não agendada
     - `get_unscheduled_task(user_id, task_id)` - Busca uma tarefa específica

4. **backend/crud_orchestrator.py** ⚠️ PRECISA AJUSTES
   - Helper `_build_agenda_html_payload` adicionado
   - Bulk delete suporta unscheduled tasks
   - **FALTA**: Integrar create/update/delete com unscheduled tasks

### O Que Ainda Falta Implementar

#### Backend (crud_orchestrator.py)

1. **Função `_create_unscheduled_task_data`** (Nova)
   - Criar tarefa sem data obrigatória
   - Retornar html_view_data com unscheduled tasks

2. **Modificar `_create_task_data`**
   - Adicionar parâmetros opcionais: `task_id`, `project_id`
   - Permitir reutilização do mesmo ID ao agendar task pendente

3. **Nova função `_update_or_schedule_unscheduled_task`**
   - Se `target_date` fornecido → agenda a tarefa (move para agenda)
   - Senão → apenas atualiza metadados

4. **Nova função `_delete_unscheduled_task_entry`**
   - Remove tarefa da subcollection unscheduled

5. **Modificar `orchestrate_crud_action`**
   - **CREATE**: Se `date` ausente → chamar `_create_unscheduled_task_data`
   - **UPDATE**: Se `date` ausente → chamar `_update_or_schedule_unscheduled_task`; se date presente mas task não existe em agenda → tentar agendar da lista unscheduled
   - **DELETE**: Se `date` ausente ou tarefa não encontrada → tentar deletar de unscheduled como fallback

#### Frontend (public/index.html)

1. **Seção de Inbox/Tarefas Pendentes**
   - Renderizar `html_view_data.agenda.unscheduled`
   - Permitir arrastar para calendário (agendar)
   - Botões: editar, deletar, marcar como concluída

2. **Formulário de Nova Tarefa**
   - Tornar data/hora opcionais
   - Se não informadas → criar como unscheduled

3. **Kanban View**
   - Coluna "Inbox" para unscheduled tasks
   - Drag & drop para outras colunas (agenda task automaticamente)

4. **Google Calendar**
   - Exibir conta conectada no profile
   - Botão de reconexão se desconectado

### Testes Necessários

1. Criar tarefa sem data via `/actions`
2. Atualizar tarefa unscheduled
3. Agendar tarefa unscheduled (update com date)
4. Deletar tarefa unscheduled
5. Bulk delete com filtro de descrição (incluindo unscheduled)
6. Frontend: renderizar inbox, criar sem data, agendar via drag

### Modelo Firestore

```
users/{userId}/unscheduled_tasks/{taskId}
  ├─ id: string
  ├─ description: string
  ├─ time: string | null
  ├─ duration_minutes: int | null
  ├─ status: "todo" | "in_progress" | "done"
  ├─ completed: boolean
  ├─ origin: "unscheduled"
  ├─ project_id: string | null
  ├─ created_at: timestamp
  └─ updated_at: timestamp
```

### Payload Exemplos

#### Criar Tarefa Não Agendada
```json
{
  "user_id": "test_user",
  "item_type": "task",
  "action": "create",
  "data": {
    "description": "Comprar leite"
  }
}
```

#### Agendar Tarefa Pendente
```json
{
  "user_id": "test_user",
  "item_type": "task",
  "action": "update",
  "item_id": "task-uuid",
  "data": {
    "date": "2025-12-01",
    "time": "10:00"
  }
}
```

#### Deletar Tarefa Não Agendada
```json
{
  "user_id": "test_user",
  "item_type": "task",
  "action": "delete",
  "item_id": "task-uuid"
}
```
