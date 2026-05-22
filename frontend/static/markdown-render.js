/**
 * Renders assistant chat text as sanitized HTML from Markdown.
 * Requires marked and DOMPurify (loaded via CDN before this script).
 */
(function () {
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function renderChatMarkdown(text) {
        if (!text) return '';

        if (typeof marked === 'undefined' || typeof DOMPurify === 'undefined') {
            return escapeHtml(text);
        }

        try {
            const rawHtml = marked.parse(text, {
                breaks: true,
                gfm: true,
            });
            return DOMPurify.sanitize(rawHtml, {
                USE_PROFILES: { html: true },
            });
        } catch (e) {
            console.warn('Markdown render failed:', e);
            return escapeHtml(text);
        }
    }

    function shouldRenderMarkdown(sender) {
        return sender === 'bot' || sender === 'agent';
    }

    function setMessageContent(element, text, sender) {
        if (shouldRenderMarkdown(sender)) {
            element.innerHTML = renderChatMarkdown(text);
            element.classList.add('message-markdown');
        } else {
            element.textContent = text;
        }
    }

    window.renderChatMarkdown = renderChatMarkdown;
    window.setChatMessageContent = setMessageContent;
})();
