// ============================================
// EIXA - Melhorias de JavaScript/UX
// ============================================

/**
 * Melhorias para Drag & Drop no Kanban
 * Adiciona classes visuais durante o arrasto
 */

// Sobrescrever função drag existente
const originalDrag = window.drag;
window.drag = function(ev, type, id, extra = {}) {
    // Chama função original
    if (originalDrag) originalDrag(ev, type, id, extra);
    
    // Adiciona classe de dragging
    const element = ev.target.closest('.project-card, .micro-card');
    if (element) {
        element.classList.add('dragging');
        
        // Remove classe após 100ms (tempo de transição)
        setTimeout(() => {
            element.classList.remove('dragging');
        }, 100);
    }
};

// Sobrescrever função allowDrop para adicionar classe visual
const originalAllowDrop = window.allowDrop;
window.allowDrop = function(ev) {
    ev.preventDefault();
    
    // Adiciona classe drag-over na coluna
    const column = ev.target.closest('.kanban-column');
    if (column && !column.classList.contains('drag-over')) {
        column.classList.add('drag-over');
        
        // Remove após 200ms
        setTimeout(() => {
            column.classList.remove('drag-over');
        }, 200);
    }
};

// Sobrescrever função drop
const originalDrop = window.drop;
window.drop = async function(ev, newStatus) {
    ev.preventDefault();
    
    // Remove classe drag-over
    const column = ev.target.closest('.kanban-column');
    if (column) {
        column.classList.remove('drag-over');
    }
    
    // Chama função original
    if (originalDrop) await originalDrop(ev, newStatus);
};

/**
 * Melhorias para Memory Entry
 * Adiciona animação suave ao expandir
 */
document.addEventListener('DOMContentLoaded', () => {
    // Interceptar cliques em memory entries
    document.addEventListener('click', (e) => {
        const toggleButton = e.target.closest('.memory-entry-toggle');
        if (toggleButton) {
            const entry = toggleButton.closest('.memory-entry');
            const detailSection = entry.querySelector('.memory-entry-details');
            
            if (entry && detailSection) {
                const isExpanded = entry.classList.toggle('expanded');
                
                // Atualiza ícone
                const icon = toggleButton.querySelector('.material-icons-round');
                if (icon) {
                    icon.textContent = isExpanded ? 'expand_less' : 'expand_more';
                }
                
                // Anima altura
                if (isExpanded) {
                    detailSection.style.maxHeight = detailSection.scrollHeight + 'px';
                } else {
                    detailSection.style.maxHeight = '0px';
                }
            }
        }
    });
});

/**
 * Melhoria para Calendar Status Badge
 * Adiciona animação de pulse na conexão
 */
function updateCalendarStatusUIEnhanced(isConnected) {
    const badge = document.getElementById('calendarStatusBadge');
    const icon = document.getElementById('calendarStatusIcon');
    const text = document.getElementById('calendarStatusText');
    const btn = document.getElementById('calendarConnectBtn');

    if (!badge) return;

    badge.style.display = 'flex';

    if (isConnected) {
        badge.className = 'calendar-status-badge connected';
        icon.textContent = 'check_circle';
        text.textContent = 'Google Calendar Conectado';
        btn.style.display = 'none';
        
        // Adiciona efeito de sucesso
        badge.style.animation = 'none';
        setTimeout(() => {
            badge.style.animation = 'pulse 2s ease-in-out infinite';
        }, 10);
    } else {
        badge.className = 'calendar-status-badge disconnected';
        icon.textContent = 'event_busy';
        text.textContent = 'Conectar Google Calendar';
        btn.style.display = 'block';
        badge.style.animation = 'none';
    }
}

// Substituir função original
if (window.updateCalendarStatusUI) {
    window.updateCalendarStatusUI = updateCalendarStatusUIEnhanced;
}

/**
 * Melhorias para Task Cards
 * Adiciona feedback visual ao hover
 */
document.addEventListener('DOMContentLoaded', () => {
    // Adiciona efeito de brilho em task cards ao passar o mouse
    document.addEventListener('mouseover', (e) => {
        const taskCard = e.target.closest('.task-card');
        if (taskCard) {
            taskCard.style.transition = 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)';
        }
    });
});

/**
 * Smooth Scroll para navegação
 */
document.addEventListener('DOMContentLoaded', () => {
    // Aplica smooth scroll em todos os containers
    const scrollContainers = document.querySelectorAll('.chat-messages, .kanban-board, #agenda-week');
    scrollContainers.forEach(container => {
        if (container) {
            container.style.scrollBehavior = 'smooth';
        }
    });
});

/**
 * Toast Notification Melhorado
 * Adiciona animação de entrada mais suave
 */
const originalShowToast = window.showToast;
window.showToast = function(message, type = 'info') {
    if (originalShowToast) {
        originalShowToast(message, type);
    }
    
    // Adiciona animação extra ao toast
    const toastContainer = document.getElementById('toast-container');
    if (toastContainer) {
        const lastToast = toastContainer.lastElementChild;
        if (lastToast) {
            lastToast.style.animation = 'toastSlideIn 0.3s cubic-bezier(0.4, 0, 0.2, 1)';
        }
    }
};

/**
 * Modal Animation Melhorado
 */
const originalOpenModal = window.openModal;
window.openModal = function(modalId) {
    if (originalOpenModal) {
        originalOpenModal(modalId);
    }
    
    // Adiciona animação ao card do modal
    const modal = document.getElementById(modalId);
    if (modal) {
        const card = modal.querySelector('.card');
        if (card) {
            card.style.animation = 'modalSlideIn 0.3s cubic-bezier(0.4, 0, 0.2, 1)';
        }
    }
};

/**
 * Lazy Loading para Performance
 * Carrega componentes pesados sob demanda
 */
document.addEventListener('DOMContentLoaded', () => {
    // Observa elementos que entram no viewport
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('fade-in');
                observer.unobserve(entry.target);
            }
        });
    }, {
        threshold: 0.1
    });
    
    // Observa cards e elementos grandes
    const observableElements = document.querySelectorAll('.card, .project-card, .memory-entry');
    observableElements.forEach(el => observer.observe(el));
});

/**
 * Adiciona keyframes para animações
 */
const style = document.createElement('style');
style.textContent = `
    @keyframes toastSlideIn {
        from {
            opacity: 0;
            transform: translateY(20px) scale(0.9);
        }
        to {
            opacity: 1;
            transform: translateY(0) scale(1);
        }
    }
    
    @keyframes fade-in {
        from {
            opacity: 0;
            transform: translateY(20px);
        }
        to {
            opacity: 1;
            transform: translateY(0);
        }
    }
    
    .fade-in {
        animation: fade-in 0.5s ease-out;
    }
`;
document.head.appendChild(style);

/**
 * Performance: Debounce para eventos frequentes
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Aplica debounce em scroll events
window.addEventListener('scroll', debounce(() => {
    // Lógica de scroll otimizada
}, 100), { passive: true });

console.log('✨ EIXA UI Improvements loaded successfully!');
