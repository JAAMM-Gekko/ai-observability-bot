document.addEventListener('DOMContentLoaded', () => {
    const PERSONA_ID = window.PERSONA_ID || 'budtender-v1';
    const SESSION_KEY = `persona_session_${PERSONA_ID}`;
    const HISTORY_KEY = `persona_history_${PERSONA_ID}`;
    const API_URL = '/persona-chat';

    let sessionId = null;
    let chatHistoryState = [];

    const chatHistory = document.getElementById('chat-history');
    const userInput = document.getElementById('user-input');
    const sendButton = document.getElementById('send-button');
    const loadingIndicator = document.getElementById('loading-indicator');

    const feedbackLog = [];

    try {
        const stored = sessionStorage.getItem(SESSION_KEY);
        if (stored) sessionId = stored;

        const storedHistory = sessionStorage.getItem(HISTORY_KEY);
        if (storedHistory) {
            const parsed = JSON.parse(storedHistory);
            if (Array.isArray(parsed)) {
                chatHistoryState = parsed;
                chatHistoryState.forEach((msg) => {
                    if (msg && typeof msg.text === 'string' && typeof msg.sender === 'string') {
                        addMessage(msg.text, msg.sender, { skipPersist: true });
                    }
                });
            }
        }
    } catch (e) {
        console.warn('Failed to restore persona chat session:', e);
        sessionId = null;
        chatHistoryState = [];
    }

    function createFeedbackBlock(messageText) {
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
                persona_id: PERSONA_ID,
                messagePreview: messageText.slice(0, 100),
                helpful,
                comment: comment || null,
            };
            feedbackLog.push(entry);
            console.log('[Persona Feedback]', entry);
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
        } else {
            messageDiv.classList.add('bot-message');
        }

        messageDiv.textContent = text;

        if (sender === 'bot' && !skipPersist) {
            const wrapper = document.createElement('div');
            wrapper.className = 'assistant-message-wrapper';
            wrapper.appendChild(messageDiv);
            wrapper.appendChild(createFeedbackBlock(text));
            chatHistory.appendChild(wrapper);
        } else {
            chatHistory.appendChild(messageDiv);
        }

        chatHistory.scrollTop = chatHistory.scrollHeight;

        if (!skipPersist) {
            chatHistoryState.push({ text, sender });
            try {
                sessionStorage.setItem(HISTORY_KEY, JSON.stringify(chatHistoryState));
            } catch (e) {
                console.warn('Failed to persist persona chat history:', e);
            }
        }
    }

    async function sendMessage() {
        const query = userInput.value.trim();
        if (query === '') return;

        addMessage(query, 'user');
        userInput.value = '';

        sendButton.disabled = true;
        loadingIndicator.style.display = 'block';

        try {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 90000);

            const response = await fetch(API_URL, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    query: query,
                    persona_id: PERSONA_ID,
                    session_id: sessionId,
                }),
                signal: controller.signal,
            });
            clearTimeout(timeoutId);

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.detail || `HTTP error! status: ${response.status}`);
            }

            const data = await response.json();

            if (data.session_id) {
                sessionId = data.session_id;
                try {
                    sessionStorage.setItem(SESSION_KEY, sessionId);
                } catch (e) {
                    console.warn('Failed to persist session id:', e);
                }
            }

            addMessage(data.answer, 'bot');
        } catch (error) {
            console.error('Error sending message:', error);
            const msg = error.name === 'AbortError'
                ? 'Request timed out. Please try again.'
                : `Sorry, something went wrong: ${error.message}`;
            addMessage(msg, 'bot');
        } finally {
            sendButton.disabled = false;
            loadingIndicator.style.display = 'none';
        }
    }

    sendButton.addEventListener('click', sendMessage);
    userInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') sendMessage();
    });

    userInput.focus();
});
