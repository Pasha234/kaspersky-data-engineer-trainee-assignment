import logging
import re

class PrivacyLogFilter(logging.Filter):
    """
    Дополнительное редактирование известных форматов чувствительных логов.

    Не заменяет отключение логирования содержимого запросов и ответов.
    Действует только на обработчики, к которым подключён фильтр.
    """
    # Matches patterns like "prompt: '...', " or "prompt_token_ids: [...]"
    PROMPT_PATTERN = re.compile(
        r"(prompt:\s*['\"].*?['\"]|prompt_token_ids:\s*\[.*?\]|output:\s*['\"].*?['\"])",
        re.DOTALL
    )

    def filter(self, record: logging.LogRecord) -> bool:
        # Format the message first so we inspect the final string
        msg = record.getMessage()

        # If vLLM logs a received request with a prompt, redact the sensitive parts
        # but keep the fact that the request was received!
        if "prompt:" in msg or "prompt_token_ids:" in msg or "Generated" in msg:
            redacted_msg = self.PROMPT_PATTERN.sub("[REDACTED_FOR_PRIVACY]", msg)
            record.msg = redacted_msg
            record.args = ()
        
        return True  # Always return True so the event itself is still logged