"""AI Intelligence Engine — dual-mode ticket analysis via DeepSeek or Phi-4."""
import json
import re
import time
from openai import OpenAI

import config

SYSTEM_PROMPT = """Ты — опытный AI-аналитик системы обработки клиентских обращений финансовой компании Freedom Finance (Казахстан).

Твоя задача — проанализировать обращение клиента и вернуть структурированный JSON.

### Формат ответа — JSON с 7 полями:

1. "type" — тип обращения, СТРОГО одно из:
   - "Жалоба" — недовольство качеством обслуживания, задержки, грубость сотрудников
   - "Смена данных" — смена номера, адреса, документов, обновление личных данных
   - "Консультация" — вопросы, запросы информации, уточнения по продуктам
   - "Претензия" — требования вернуть деньги, угрозы судом, официальные претензии
   - "Неработоспособность приложения" — баги, ошибки входа, зависания, технические сбои
   - "Мошеннические действия" — подозрение на мошенничество, фишинг, неизвестные списания
   - "Спам" — рекламные, нерелевантные или бессмысленные сообщения

   Решающие границы (Tie-breakers):
   Если текст подходит под несколько категорий, применяй приоритет (сверху вниз):
   1. "Мошеннические действия" — если есть признаки мошенничества/несанкционированных операций (даже если есть жалоба/требование).
   2. "Претензия" — если есть требование возврата денег/угроза судом.
   3. "Неработоспособность приложения" — если основная проблема — техническая недоступность (ошибка, не входит, зависает) без упора на возврат.
   4. "Смена данных" — запрос на обновление информации.
   5. "Жалоба" — явный негатив или недовольство.
   6. "Консультация" — обычный вопрос.
   7. "Спам" — сообщение не по теме.

2. "priority" — приоритет от 1 (низкий) до 10 (критический):
   - 1-3: общие вопросы, консультации, смена данных без срочности
   - 4-6: технические проблемы, стандартные жалобы, смена документов
   - 7-8: серьёзные жалобы с финансовыми потерями, претензии с дедлайнами
   - 9-10: мошенничество, блокировка счетов, крупные потери денег, угроза безопасности

3. "language" — язык обращения, СТРОГО одно из: "RU", "KZ", "ENG"

4. "sentiment" — тональность, СТРОГО одно из: "Позитивный", "Нейтральный", "Негативный"

5. "normalized_address" — извлечь город и улицу из текста (если упоминаются). Формат: "Город, улица" или "Unknown" если нет адреса. Не выдумывай адрес. Используй только то, что явно написано. Если есть только город без улицы — верни "Город, Unknown".

6. "summary" — краткое резюме обращения (1-2 предложения, ВСЕГДА НА РУССКОМ) + рекомендуемые следующие действия для менеджера.

7. "confidence" — твоя уверенность в правильности классификации, число от 0.0 до 1.0:
   - 0.9-1.0: однозначный случай, текст чётко соответствует одной категории
   - 0.7-0.9: высокая уверенность, но возможна альтернативная интерпретация
   - 0.5-0.7: текст неоднозначный, может относиться к нескольким категориям
   - <0.5: текст слишком короткий, неясный или противоречивый

### Примеры:

Обращение: "Здравствуйте, я из Караганды, улица Бухар Жирау 52. Вчера мне пришло SMS о списании 150 000 тенге, но я ничего не покупал. Прошу немедленно заблокировать карту!"
Ответ:
{"type": "Мошеннические действия", "priority": 10, "language": "RU", "sentiment": "Негативный", "normalized_address": "Караганда, улица Бухар Жирау 52", "summary": "Клиент сообщает о несанкционированном списании 150 000 тенге. Необходимо срочно заблокировать карту и инициировать расследование.", "confidence": 0.95}

Обращение: "Не могу войти в приложение Freedom Finance, пишет ошибку 503. Пробовал переустановить — не помогло."
Ответ:
{"type": "Неработоспособность приложения", "priority": 5, "language": "RU", "sentiment": "Негативный", "normalized_address": "Unknown", "summary": "Ошибка 503 при входе в приложение, переустановка не помогла. Передать в техподдержку для проверки серверной стороны.", "confidence": 0.92}

Обращение: "Купите наш продукт со скидкой 50%! Лучшее предложение года! Ссылка: example.com"
Ответ:
{"type": "Спам", "priority": 1, "language": "RU", "sentiment": "Нейтральный", "normalized_address": "Unknown", "summary": "Рекламное сообщение, не связанное с финансовыми услугами. Маркировать как спам.", "confidence": 0.98}

### Инструкция:
Прочитай обращение внимательно. Сначала определи тип и приоритет, затем остальные поля.
Верни ТОЛЬКО валидный JSON без маркдаун-форматирования, без ```json``` блоков, без пояснений. Только чистый JSON."""

USER_PROMPT_TEMPLATE = """Обращение клиента:
\"\"\"
{description}
\"\"\"

Проанализируй и верни JSON."""

MAX_RETRIES = 2  # Number of retry attempts before fallback


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

        Returns dict with: type, priority, language, sentiment, normalized_address,
        summary, and optional flags (ai_fallback, ai_schema_error, needs_clarification, etc).
        """
        if not description or len(description.strip()) < 10:
            result = self._fallback_analysis()
            result["needs_clarification"] = True
            result["priority"] = 3  # Lower priority for empty/garbage text
            result["type"] = "Консультация" # Задано правило для коротких текстов
            result["summary"] = "Пустое или неясное обращение; требуется уточнение у клиента."
            return result

        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": USER_PROMPT_TEMPLATE.format(description=description[:2000])},
                    ],
                    temperature=0.1,
                    max_tokens=800,
                    timeout=8.0,  # Enforce strict 8s timeout to meet SLA
                )

                raw = response.choices[0].message.content.strip()
                result = self._parse_response(raw)

                # If parse returned a fallback due to schema error, retry once more
                if result.get("ai_schema_error") and attempt < MAX_RETRIES:
                    last_error = "ai_schema_error"
                    continue

                return result

            except Exception as e:
                last_error = str(e)
                print(f"[AI ERROR] Attempt {attempt + 1}/{MAX_RETRIES + 1}: {e}")
                if attempt < MAX_RETRIES:
                    time.sleep(0.5 * (attempt + 1))  # Brief backoff
                    continue

        # All retries exhausted — return fallback
        print(f"[AI ERROR] All {MAX_RETRIES + 1} attempts failed. Using fallback. Last error: {last_error}")
        return self._fallback_analysis()

    def _parse_response(self, raw: str) -> dict:
        """Parse the AI response into a structured dict, with fallbacks."""
        # Remove markdown code blocks if present
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```\s*", "", raw)
        raw = raw.strip()

        data = None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Try to extract JSON from the response
            match = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    pass

        if data is None:
            result = self._fallback_analysis()
            result["ai_schema_error"] = True
            return result

        # Validate and normalize fields
        valid_types = [
            "Жалоба", "Смена данных", "Консультация", "Претензия",
            "Неработоспособность приложения", "Мошеннические действия", "Спам"
        ]
        valid_languages = ["RU", "KZ", "ENG"]
        valid_sentiments = ["Позитивный", "Нейтральный", "Негативный"]

        result = {
            "type": data.get("type", "Консультация"),
            "priority": data.get("priority", 5),
            "language": data.get("language", "RU"),
            "sentiment": data.get("sentiment", "Нейтральный"),
            "normalized_address": data.get("normalized_address", "Unknown"),
            "summary": data.get("summary", "Резюме недоступно."),
            "confidence": data.get("confidence", 0.5),
        }

        # Validate type — flag low confidence if unknown
        if result["type"] not in valid_types:
            result["type"] = "Консультация"
            result["ai_type_low_confidence"] = True

        # Clamp priority
        if not isinstance(result["priority"], (int, float)) or not (1 <= result["priority"] <= 10):
            result["priority"] = 5
        else:
            result["priority"] = int(result["priority"])

        # Validate language — default RU
        if result["language"] not in valid_languages:
            result["language"] = "RU"
            result["language_confidence"] = "low"

        # Validate sentiment
        if result["sentiment"] not in valid_sentiments:
            result["sentiment"] = "Нейтральный"

        # Clamp confidence
        try:
            conf = float(result["confidence"])
            result["confidence"] = max(0.0, min(1.0, conf))
        except (ValueError, TypeError):
            result["confidence"] = 0.5

        return result

    def _fallback_analysis(self) -> dict:
        """Return a default analysis when AI fails."""
        return {
            "type": "Консультация",
            "priority": 5,
            "language": "RU",
            "sentiment": "Нейтральный",
            "normalized_address": "Unknown",
            "summary": "AI недоступен; требуется ручная проверка обращения.",
            "confidence": 0.0,
            "ai_fallback": True,
        }
