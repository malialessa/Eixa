
// ============================================
// EIXA - Tools Menu Logic
// ============================================

document.addEventListener('DOMContentLoaded', () => {
    const toolsBtn = document.getElementById('toolsBtn');
    const toolsMenu = document.getElementById('tools-menu');
    const closeToolsBtn = document.querySelector('.close-tools-btn');

    if (toolsBtn && toolsMenu) {
        // Toggle menu visibility
        toolsBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const isVisible = toolsMenu.style.display === 'block';
            toolsMenu.style.display = isVisible ? 'none' : 'block';
        });

        // Close menu
        if (closeToolsBtn) {
            closeToolsBtn.addEventListener('click', () => {
                toolsMenu.style.display = 'none';
            });
        }

        // Close when clicking outside
        document.addEventListener('click', (e) => {
            if (toolsMenu.style.display === 'block' && 
                !toolsMenu.contains(e.target) && 
                e.target !== toolsBtn &&
                !toolsBtn.contains(e.target)) {
                toolsMenu.style.display = 'none';
            }
        });
    }
});

// Function to send commands from tools menu
window.sendToolCommand = function(commandText) {
    const messageInput = document.getElementById('message-input');
    const sendBtn = document.getElementById('send-btn');
    const toolsMenu = document.getElementById('tools-menu');

    if (messageInput && sendBtn) {
        // Set input value
        messageInput.value = commandText;
        
        // Trigger send
        sendBtn.click();
        
        // Close menu
        if (toolsMenu) {
            toolsMenu.style.display = 'none';
        }
    }
};
