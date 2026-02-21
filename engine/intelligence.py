"""AI Intelligence Engine — dual-mode ticket analysis via DeepSeek or Phi-4."""
import json
import re
from openai import OpenAI

import config

SYSTEM_PROMPT = """Ты — AI ассистент системы обработки клиентских обращений финансовой компании Freedom Finance.

Проанализируй обращение клиента и верни JSON с полями:

1. "type" — тип обращения, СТРОГО одно из:
   - "Жалоба" (жалобы, недовольство обслуживанием)
   - "Смена данных" (смена номера, адреса, документов, обновление данных)
   - "Консультация" (вопросы, запросы информации)
   - "Претензия" (требования вернуть деньги, угрозы судом, официальные претензии)
   - "Неработоспособность приложения" (баги, ошибки входа, технические проблемы)
   - "Мошеннические действия" (подозрение на мошенничество, фишинг, неизвестные операции)
   - "Спам" (рекламные, нерелевантные сообщения)

2. "priority" — приоритет от 1 (низкий) до 10 (критический):
   - 1-3: обычные вопросы, консультации
   - 4-6: технические проблемы, смена данных
   - 7-8: жалобы, претензии
   - 9-10: мошенничество, блокировка счетов, потеря денег

3. "language" — язык обращения, СТРОГО одно из: "RU", "KZ", "ENG"

4. "sentiment" — тональность, СТРОГО одно из: "Positive", "Neutral", "Negative"

5. "normalized_address" — извлечь город и улицу из текста обращения (если упоминаются). Формат: "Город, улица" или "Unknown" если нет адреса в тексте.

ВАЖНО: Верни ТОЛЬКО валидный JSON без маркдаун-форматирования, без ```json``` блоков, без пояснений. Только чистый JSON."""

USER_PROMPT_TEMPLATE = """Обращение клиента:
\"\"\"
{description}
\"\"\"

Верни JSON анализ этого обращения."""


class IntelligenceEngine:
    """Dual-mode AI engine for ticket enrichment."""

    def __init__(self, mode: str = "deepseek"):
        """
        Initialize the engine.

        Args:
            mode: "deepseek" for DeepSeek API, "phi4" for local Ollama Phi-4.
        """
        self.mode = mode

        if mode == "deepseek":
            self.client = OpenAI(
                api_key=config.DEEPSEEK_API_KEY,
                base_url="https://api.deepseek.com",
            )
            self.model = "deepseek-chat"
        elif mode == "phi4":
            self.client = OpenAI(
                api_key="ollama",  # Ollama doesn't need a real key
                base_url=f"{config.OLLAMA_BASE_URL}/v1",
            )
            self.model = "phi4"
        else:
            raise ValueError(f"Unknown mode: {mode}. Use 'deepseek' or 'phi4'.")

    def analyze_ticket(self, description: str) -> dict:
        """
        Analyze a ticket description and return structured data.

        Returns dict with: type, priority, language, sentiment, normalized_address.
        """
        if not description or not description.strip():
            return self._fallback_analysis()

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": USER_PROMPT_TEMPLATE.format(description=description[:2000])},
                ],
                temperature=0.1,
                max_tokens=500,
            )

            raw = response.choices[0].message.content.strip()
            return self._parse_response(raw)
        except Exception as e:
            print(f"[AI ERROR] {e}")
            return self._fallback_analysis()

    def _parse_response(self, raw: str) -> dict:
        """Parse the AI response into a structured dict, with fallbacks."""
        # Remove markdown code blocks if present
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```\s*", "", raw)
        raw = raw.strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Try to extract JSON from the response
            match = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    return self._fallback_analysis()
            else:
                return self._fallback_analysis()

        # Validate and normalize fields
        valid_types = [
            "Жалоба", "Смена данных", "Консультация", "Претензия",
            "Неработоспособность приложения", "Мошеннические действия", "Спам"
        ]
        valid_languages = ["RU", "KZ", "ENG"]
        valid_sentiments = ["Positive", "Neutral", "Negative"]

        result = {
            "type": data.get("type", "Консультация"),
            "priority": data.get("priority", 5),
            "language": data.get("language", "RU"),
            "sentiment": data.get("sentiment", "Neutral"),
            "normalized_address": data.get("normalized_address", "Unknown"),
        }

        # Clamp values
        if result["type"] not in valid_types:
            result["type"] = "Консультация"
        if not isinstance(result["priority"], int) or not (1 <= result["priority"] <= 10):
            result["priority"] = 5
        if result["language"] not in valid_languages:
            result["language"] = "RU"
        if result["sentiment"] not in valid_sentiments:
            result["sentiment"] = "Neutral"

        return result

    def _fallback_analysis(self) -> dict:
        """Return a default analysis when AI fails."""
        return {
            "type": "Консультация",
            "priority": 5,
            "language": "RU",
            "sentiment": "Neutral",
            "normalized_address": "Unknown",
        }
