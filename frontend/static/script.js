document.addEventListener('DOMContentLoaded', () => {
    const SESSION_STORAGE_KEY = 'faq_session_id';
    const HISTORY_STORAGE_KEY = 'faq_chat_history';

    let sessionId = null;
    let websocket = null;
    let isConnectedToAgent = false;
    let typingTimeout = null;
    let chatHistoryState = [];

    const chatHistory = document.getElementById('chat-history');
    const userInput = document.getElementById('user-input');
    const sendButton = document.getElementById('send-button');
    const loadingIndicator = document.getElementById('loading-indicator');
    const chatWidgetButton = document.getElementById('chat-widget-button');
    const chatContainer = document.getElementById('chat-container');
    const closeButton = document.getElementById('close-button');
    const agentStatus = document.getElementById('agent-status');
    const agentNameSpan = document.getElementById('agent-name');
    const typingIndicator = document.getElementById('typing-indicator');

    const API_URL = '/chat';

    // Mock telemetry store for feedback (queryable / exportable later, e.g. Splunk)
    const feedbackLog = [];

    // Restore session and chat history for this browser tab (persists across refresh, cleared when tab closes)
    try {
        const storedSessionId = sessionStorage.getItem(SESSION_STORAGE_KEY);
        if (storedSessionId) {
            sessionId = storedSessionId;
            console.log('Restored existing chat session from sessionStorage:', sessionId);
        }

        const storedHistoryRaw = sessionStorage.getItem(HISTORY_STORAGE_KEY);
        if (storedHistoryRaw) {
            const parsed = JSON.parse(storedHistoryRaw);
            if (Array.isArray(parsed)) {
                chatHistoryState = parsed;
                // Rehydrate UI without re-saving to storage
                chatHistoryState.forEach((msg) => {
                    if (msg && typeof msg.text === 'string' && typeof msg.sender === 'string') {
                        addMessage(msg.text, msg.sender, { skipPersist: true });
                    }
                });
            }
        }
    } catch (e) {
        console.warn('Failed to restore chat session from sessionStorage:', e);
        sessionId = null;
        chatHistoryState = [];
        try {
            sessionStorage.removeItem(SESSION_STORAGE_KEY);
            sessionStorage.removeItem(HISTORY_STORAGE_KEY);
        } catch {
            // Ignore storage cleanup errors
        }
    }

    function createFeedbackBlock(messageText, sender) {
        const block = document.createElement('div');
        block.className = 'message-feedback';
        block.innerHTML = [
            '<span class="feedback-prompt">Was this helpful?</span>',
            '<div class="feedback-buttons">',
            '  <button type="button" class="feedback-btn feedback-btn-yes" aria-label="Yes, helpful">👍</button>',
            '  <button type="button" class="feedback-btn feedback-btn-no" aria-label="No, not helpful">👎</button>',
            '</div>',
            '<div class="feedback-comment-wrap" style="display:none">',
            '  <input type="text" class="feedback-comment-input" placeholder="Tell us more (optional)" maxlength="500">',
            '  <button type="button" class="feedback-comment-submit">Submit</button>',
            '</div>',
            '<span class="feedback-thanks" style="display:none">Thanks for your feedback!</span>',
        ].join('');

        const prompt = block.querySelector('.feedback-prompt');
        const buttons = block.querySelector('.feedback-buttons');
        const commentWrap = block.querySelector('.feedback-comment-wrap');
        const commentInput = block.querySelector('.feedback-comment-input');
        const commentSubmit = block.querySelector('.feedback-comment-submit');
        const thanks = block.querySelector('.feedback-thanks');

        function submitFeedback(helpful, comment) {
            const entry = {
                timestamp: new Date().toISOString(),
                sender,
                messagePreview: messageText.slice(0, 100) + (messageText.length > 100 ? '…' : ''),
                helpful,
                comment: comment || null,
            };
            feedbackLog.push(entry);
            console.log('[Feedback telemetry]', entry);
            prompt.style.display = 'none';
            buttons.style.display = 'none';
            commentWrap.style.display = 'none';
            thanks.style.display = 'inline';
        }

        block.querySelector('.feedback-btn-yes').addEventListener('click', () => {
            submitFeedback(true);
        });

        block.querySelector('.feedback-btn-no').addEventListener('click', () => {
            buttons.style.display = 'none';
            commentWrap.style.display = 'block';
            commentInput.focus();
        });

        commentSubmit.addEventListener('click', () => {
            submitFeedback(false, commentInput.value.trim() || null);
        });
        commentInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') commentSubmit.click();
        });

        return block;
    }

    function addMessage(text, sender, options = {}) {
        const { skipPersist = false } = options;
        const messageDiv = document.createElement('div');
        messageDiv.classList.add('message');

        if (sender === 'user') {
            messageDiv.classList.add('user-message');
        } else if (sender === 'bot') {
            // If we're currently connected to a live agent,
            // visually treat these as agent messages so they stand out.
            if (isConnectedToAgent) {
                messageDiv.classList.add('agent-message');
            } else {
                messageDiv.classList.add('bot-message');
            }
        } else if (sender === 'agent') {
            messageDiv.classList.add('agent-message');
        } else if (sender === 'system') {
            messageDiv.classList.add('system-message');
        }

        if (typeof window.setChatMessageContent === 'function') {
            window.setChatMessageContent(messageDiv, text, sender);
        } else {
            messageDiv.textContent = text;
        }

        /* Feedback "Was this helpful?" only for bot replies, not startup message or live agent messages */
        if (sender === 'bot') {
            const wrapper = document.createElement('div');
            wrapper.className = 'assistant-message-wrapper';
            wrapper.appendChild(messageDiv);
            wrapper.appendChild(createFeedbackBlock(text, sender));
            chatHistory.appendChild(wrapper);
        } else {
            chatHistory.appendChild(messageDiv);
        }

        chatHistory.scrollTop = chatHistory.scrollHeight;

        if (!skipPersist) {
            chatHistoryState.push({ text, sender });
            try {
                sessionStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(chatHistoryState));
            } catch (e) {
                console.warn('Failed to persist chat history to sessionStorage:', e);
            }
        }
    }

    // Typing indicator for customer
    userInput.addEventListener('input', () => {
        if (websocket && websocket.readyState === WebSocket.OPEN) {
            // Send typing indicator
            websocket.send(JSON.stringify({
                type: 'typing',
                is_typing: true
            }));

            // Clear previous timeout
            clearTimeout(typingTimeout);

            // Stop typing after 2 seconds of inactivity
            typingTimeout = setTimeout(() => {
                if (websocket && websocket.readyState === WebSocket.OPEN) {
                    websocket.send(JSON.stringify({
                        type: 'typing',
                        is_typing: false
                    }));
                }
            }, 2000);
        }
    });

    function renderProductCards(cards) {
        const cardsContainer = document.createElement('div');
        cardsContainer.className = 'chat-product-cards';

        cards.forEach(card => {
            const cardEl = document.createElement('div');
            cardEl.className = 'chat-product-card';
            cardEl.innerHTML = `
                <img src="${card.image}" alt="${card.name}" loading="lazy">
                <div class="chat-product-card-info">
                    <div class="chat-product-card-name">${card.name}</div>
                    <div class="chat-product-card-category">${card.category}</div>
                    <div class="chat-product-card-price">${card.price}</div>
                </div>
            `;
            cardsContainer.appendChild(cardEl);
        });

        chatHistory.appendChild(cardsContainer);
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    async function sendMessage() {
        const query = userInput.value.trim();
        if (query === '') return;

        addMessage(query, 'user');
        userInput.value = '';

        // If connected to live agent via WebSocket, send through WebSocket
        if (isConnectedToAgent && websocket && websocket.readyState === WebSocket.OPEN) {
            websocket.send(JSON.stringify({
                type: 'message',
                content: query
            }));
            return;
        }

        sendButton.disabled = true;
        loadingIndicator.style.display = 'block';

        try {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 90000); // 90s timeout
            const response = await fetch(API_URL, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    query: query,
                    session_id: sessionId
                }),
                signal: controller.signal,
            });
            clearTimeout(timeoutId);

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.detail || `HTTP error! status: ${response.status}`);
            }

            const data = await response.json();

            // Update session ID and persist it for this tab
            if (data.session_id) {
                sessionId = data.session_id;
                try {
                    sessionStorage.setItem(SESSION_STORAGE_KEY, sessionId);
                } catch (e) {
                    console.warn('Failed to persist session id to sessionStorage:', e);
                }
            }

            addMessage(data.answer, 'bot');

            // Render product cards if returned
            if (data.cards && data.cards.length > 0) {
                renderProductCards(data.cards);
            }

            // If state changed to waiting_for_agent, establish WebSocket
            if (data.state === 'waiting_for_agent') {
                connectWebSocket();
            }
        } catch (error) {
            console.error('Error sending message:', error);
            const msg = error.name === 'AbortError'
                ? 'Request timed out. The server may be slow. Please try again.'
                : `Sorry, there was an error processing your request: ${error.message}`;
            addMessage(msg, 'bot');
        } finally {
            sendButton.disabled = false;
            loadingIndicator.style.display = 'none';
            userInput.focus();
        }
    }

    function connectWebSocket() {
        if (!sessionId) {
            console.error('No session ID available');
            return;
        }

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/customer/${sessionId}`;

        console.log('Connecting to WebSocket:', wsUrl);
        websocket = new WebSocket(wsUrl);

        websocket.onopen = () => {
            console.log('WebSocket connected');
        };

        websocket.onmessage = (event) => {
            const data = JSON.parse(event.data);
            console.log('WebSocket message:', data);

            switch (data.type) {
                case 'agent_joined':
                    isConnectedToAgent = true;
                    agentStatus.style.display = 'flex';
                    agentNameSpan.textContent = data.agent_name;
                    addMessage(data.message, 'system');
                    break;

                case 'agent_message':
                    addMessage(`${data.agent_name || 'Agent'}: ${data.content}`, 'agent');
                    break;

                case 'agent_left':
                    isConnectedToAgent = data.return_to_ai || false;
                    if (!isConnectedToAgent) {
                        agentStatus.style.display = 'none';
                    }
                    addMessage(data.message, 'system');
                    break;

                case 'agent_typing':
                    if (data.is_typing) {
                        typingIndicator.style.display = 'flex';
                    } else {
                        typingIndicator.style.display = 'none';
                    }
                    break;

                case 'ping':
                    // Server keep-alive ping — no action needed
                    break;

                case 'timeout':
                    // Agent wait timed out — server already returned session to AI mode
                    isConnectedToAgent = false;
                    agentStatus.style.display = 'none';
                    addMessage(data.message, 'system');
                    break;

                case 'error':
                    addMessage(data.message, 'system');
                    break;
            }
        };

        websocket.onerror = (error) => {
            console.error('WebSocket error:', error);
        };

        websocket.onclose = () => {
            console.log('WebSocket disconnected');
            // Attempt to reconnect after 3 seconds if still connected to agent
            if (isConnectedToAgent) {
                setTimeout(() => {
                    console.log('Attempting to reconnect...');
                    connectWebSocket();
                }, 3000);
            }
        };
    }

    chatWidgetButton.addEventListener('click', () => {
        chatContainer.classList.remove('chat-container-closed');
        chatContainer.classList.add('chat-container-open');
        userInput.focus();
    });

    const heroChatCta = document.getElementById('hero-chat-cta');
    if (heroChatCta) {
        heroChatCta.addEventListener('click', (event) => {
            event.preventDefault();
            chatContainer.classList.remove('chat-container-closed');
            chatContainer.classList.add('chat-container-open');
            userInput.focus();
        });
    }

    closeButton.addEventListener('click', () => {
        chatContainer.classList.remove('chat-container-open');
        chatContainer.classList.add('chat-container-closed');
    });

    sendButton.addEventListener('click', sendMessage);
    userInput.addEventListener('keypress', (event) => {
        if (event.key === 'Enter') {
            sendMessage();
        }
    });

    /* No feedback on startup message – leave initial bot message as plain bubble */

    // Resize handle: drag top-left corner to expand/shrink the widget
    const resizeHandle = document.getElementById('chat-resize-handle');
    if (resizeHandle) {
        let isResizing = false;
        let startX, startY, startWidth, startHeight, startRight, startBottom;

        resizeHandle.addEventListener('mousedown', initResize);
        resizeHandle.addEventListener('touchstart', initResize, { passive: false });

        function initResize(e) {
            e.preventDefault();
            isResizing = true;
            chatContainer.classList.add('chat-resizing');

            const point = e.touches ? e.touches[0] : e;
            startX = point.clientX;
            startY = point.clientY;

            const rect = chatContainer.getBoundingClientRect();
            startWidth = rect.width;
            startHeight = rect.height;
            startRight = window.innerWidth - rect.right;
            startBottom = window.innerHeight - rect.bottom;

            document.addEventListener('mousemove', doResize);
            document.addEventListener('mouseup', stopResize);
            document.addEventListener('touchmove', doResize, { passive: false });
            document.addEventListener('touchend', stopResize);
        }

        function doResize(e) {
            if (!isResizing) return;
            e.preventDefault();

            const point = e.touches ? e.touches[0] : e;
            const dx = startX - point.clientX;
            const dy = startY - point.clientY;

            const newWidth = Math.min(Math.max(startWidth + dx, 300), window.innerWidth - 40);
            const newHeight = Math.min(Math.max(startHeight + dy, 350), window.innerHeight - 40);

            chatContainer.style.width = newWidth + 'px';
            chatContainer.style.height = newHeight + 'px';
            chatContainer.style.maxWidth = newWidth + 'px';
            chatContainer.style.maxHeight = newHeight + 'px';
        }

        function stopResize() {
            isResizing = false;
            chatContainer.classList.remove('chat-resizing');
            document.removeEventListener('mousemove', doResize);
            document.removeEventListener('mouseup', stopResize);
            document.removeEventListener('touchmove', doResize);
            document.removeEventListener('touchend', stopResize);
        }
    }

    // Expose mock feedback log for inspection/export (e.g. Splunk correlation)
    window.__feedbackLog = feedbackLog;

    // Mobile: adjust layout when virtual keyboard opens/closes
    if (window.visualViewport) {
        window.visualViewport.addEventListener('resize', () => {
            if (chatContainer.classList.contains('chat-container-open')) {
                chatContainer.style.height = window.visualViewport.height + 'px';
                chatHistory.scrollTop = chatHistory.scrollHeight;
            }
        });
        window.visualViewport.addEventListener('scroll', () => {
            if (chatContainer.classList.contains('chat-container-open')) {
                chatContainer.style.height = window.visualViewport.height + 'px';
            }
        });
    }

    console.log('Chat widget initialized');
});