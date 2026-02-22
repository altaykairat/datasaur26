"""OCR utility for extracting text from ticket screenshots/images.

Uses Tesseract OCR via pytesseract to extract readable text from images.
Supports base64-encoded images (from CSV), file paths, filenames (resolved
from input/attachments/), and Streamlit uploaded files.
"""
import base64
import io
import logging
import os

logger = logging.getLogger("fire.ocr")

try:
    import pytesseract
    from PIL import Image
    _OCR_AVAILABLE = True
except ImportError:
    _OCR_AVAILABLE = False

# Supported image extensions
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp", ".gif"}

# Default folder for CSV-referenced attachments
ATTACHMENTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "input", "attachments")


def is_ocr_available() -> bool:
    """Check if OCR dependencies are installed."""
    return _OCR_AVAILABLE


def resolve_image_path(source: str) -> str | None:
    """
    Resolve an image source to an absolute file path.

    Checks: 
    1. Absolute path
    2. Relative to CWD
    3. input/attachments/<filename>
    4. input/attachments/<relative_to_source>
    
    Returns the resolved absolute path or None.
    """
    if not source:
        return None

    source = source.strip()

    # 1. & 2. Try as absolute or relative path
    if os.path.isfile(source):
        return os.path.abspath(source)

    # 3. Try input/attachments/ + pure filename
    filename = os.path.basename(source)
    candidate = os.path.join(ATTACHMENTS_DIR, filename)
    if os.path.isfile(candidate):
        return os.path.abspath(candidate)
    
    # 4. Try input/attachments/ + original relative path (if source was like 'subdir/img.png')
    candidate_rel = os.path.join(ATTACHMENTS_DIR, source)
    if os.path.isfile(candidate_rel):
        return os.path.abspath(candidate_rel)

    return None


def extract_text_from_image(image_source: str, lang: str = "rus+eng+kaz") -> dict:
    """
    Extract text from an image using Tesseract OCR.

    Args:
        image_source: base64 string, file path, or filename (resolved from input/attachments/).
        lang: Tesseract language codes (default: Russian + English + Kazakh).

    Returns:
        dict with 'text', 'success', 'source_type', 'error'.
    """
    if not _OCR_AVAILABLE:
        return {"text": "", "success": False, "source_type": "unknown",
                "error": "pytesseract or Pillow not installed"}

    if not image_source or not str(image_source).strip():
        return {"text": "", "success": False, "source_type": "unknown",
                "error": "empty image source"}

    image_source = str(image_source).strip()

    try:
        image = _load_image(image_source)
        if image is None:
            return {"text": "", "success": False, "source_type": _detect_source_type(image_source),
                    "error": "could not load image from source"}

        lang = _resolve_lang(lang)

        # Multi-pass OCR: run on multiple image variants to catch overlay popups
        all_lines = []
        seen_lines = set()
        for variant in _image_variants(image):
            text = pytesseract.image_to_string(variant, lang=lang).strip()
            if text:
                for line in text.split("\n"):
                    stripped = line.strip()
                    if stripped and len(stripped) >= 3:
                        if stripped not in seen_lines:
                            all_lines.append(stripped)
                            seen_lines.add(stripped)

        source_type = _detect_source_type(image_source)

        if not all_lines:
            return {"text": "", "success": False, "source_type": source_type,
                    "error": "no text detected in image"}

        combined = "\n".join(all_lines)
        logger.info(f"OCR extracted {len(combined)} chars ({len(all_lines)} unique lines) from {source_type}")
        return {"text": combined, "success": True, "source_type": source_type, "error": None}

    except Exception as e:
        logger.warning(f"OCR failed: {e}")
        return {"text": "", "success": False, "source_type": _detect_source_type(image_source),
                "error": str(e)}


def extract_text_from_uploaded_file(uploaded_file, lang: str = "rus+eng+kaz") -> dict:
    """
    Extract text from a Streamlit UploadedFile object.

    Args:
        uploaded_file: Streamlit st.file_uploader result.
        lang: Tesseract language codes.

    Returns:
        dict with 'text', 'success', 'source_type', 'error'.
    """
    if not _OCR_AVAILABLE:
        return {"text": "", "success": False, "source_type": "upload",
                "error": "pytesseract or Pillow not installed"}

    if uploaded_file is None:
        return {"text": "", "success": False, "source_type": "upload",
                "error": "no file uploaded"}

    try:
        image = Image.open(uploaded_file)
        lang = _resolve_lang(lang)
        text = pytesseract.image_to_string(image, lang=lang).strip()

        if not text:
            return {"text": "", "success": False, "source_type": "upload",
                    "error": "no text detected in image"}

        logger.info(f"OCR extracted {len(text)} chars from uploaded file")
        return {"text": text, "success": True, "source_type": "upload", "error": None}

    except Exception as e:
        logger.warning(f"OCR from upload failed: {e}")
        return {"text": "", "success": False, "source_type": "upload", "error": str(e)}


def combine_description_with_ocr(description: str, ocr_text: str) -> str:
    """
    Combine human description with OCR-extracted text using a clear separator.

    Cleans OCR text to remove UI noise and labels it for the LLM.
    """
    description = (description or "").strip()
    ocr_text = _clean_ocr_text(ocr_text)

    if description and ocr_text:
        return (
            f"{description}\n\n---\n"
            f"[Текст с приложенного скриншота (OCR). "
            f"ВАЖНО: ниже может быть шум интерфейса — найди сообщение об ошибке, "
            f"статус операции или жалобу клиента]:\n{ocr_text}"
        )
    elif ocr_text:
        return (
            f"[Обращение содержит только скриншот (текст извлечён через OCR). "
            f"Среди интерфейсных элементов найди ключевую проблему клиента]:\n{ocr_text}"
        )
    else:
        return description


# ---- Common UI words to filter from OCR ----
_UI_NOISE_WORDS = {
    "главная", "меню", "войти", "выход", "назад", "далее", "закрыть",
    "поиск", "настройки", "профиль", "уведомления", "home", "menu",
    "back", "close", "settings", "search", "login", "logout", "sign in",
    "notifications", "loading", "copyright", "все права защищены",
    "подробнее", "читать далее", "подписаться", "отписаться",
}

# Keywords that signal a real issue (error, problem, complaint)
_SIGNAL_KEYWORDS = {
    # Russian
    "ошибка", "error", "не удалось", "не работает", "заблокирован",
    "сбой", "отказано", "недоступен", "невозможно", "проблема",
    "отклонён", "отклонен", "истёк", "истек", "не найден",
    "нет доступа", "неверный", "некорректн", "сервис недоступен",
    "списание", "списано", "возврат", "задолженность",
    "обратитесь", "свяжитесь", "поддержк",
    # English
    "failed", "unavailable", "denied", "blocked", "rejected",
    "timeout", "expired", "invalid", "unauthorized", "forbidden",
    "service unavailable", "internal server error", "bad request",
    "not found", "connection refused",
}

# Patterns that indicate financial/market noise
_NOISE_PATTERNS = {
    "kzt", "usd", "eur", "rub",  # Currency codes
    "тенге", "доллар",
    "+%", "-%",  # Stock ticker changes
    "акции", "акция", "индекс", "котировк", "биржа",
    "рынок", "торги", "дивиденд",
}

# Max OCR text length to send to LLM
_MAX_OCR_CHARS = 600


def _clean_ocr_text(raw: str) -> str:
    """
    Clean OCR text — find signal keywords and extract them with surrounding context.

    For each line containing a signal keyword, we keep that line plus ±2 neighbors.
    If no signal keywords found, we return the top few non-noise lines to provide
    general context about what the user is looking at.
    """
    if not raw:
        return ""

    raw_lines = raw.strip().split("\n")
    # Filter out completely empty/tiny lines but keep indexing
    lines = [(i, line.strip()) for i, line in enumerate(raw_lines) if line.strip() and len(line.strip()) >= 3]

    if not lines:
        return ""

    # Find indices of signal lines
    signal_indices = set()
    for idx, (orig_i, line) in enumerate(lines):
        lower = line.lower()
        if any(kw in lower for kw in _SIGNAL_KEYWORDS):
            signal_indices.add(idx)

    if signal_indices:
        # Expand to ±2 context window around each signal line
        keep_indices = set()
        for sig_idx in signal_indices:
            for offset in range(-2, 3):  # -2, -1, 0, +1, +2
                candidate = sig_idx + offset
                if 0 <= candidate < len(lines):
                    keep_indices.add(candidate)

        # Build result preserving order, deduplicating
        result_lines = []
        for idx in sorted(keep_indices):
            _, line = lines[idx]
            # Prefix the actual signal lines so the LLM pays attention
            prefix = ">>> " if idx in signal_indices else ""
            if (prefix + line) not in result_lines and line not in result_lines:
                result_lines.append(prefix + line)

        result = "\n".join(result_lines)
    else:
        # No error signals found. Extract general context (first ~6 valid lines)
        context_lines = []
        for _, line in lines:
            lower = line.lower()
            if lower in _UI_NOISE_WORDS:
                continue
            if len(set(line)) <= 2 and not any(c.isalnum() for c in line):
                continue
            
            context_lines.append(line)
            if len(context_lines) >= 6:
                break
                
        result = "\n".join(context_lines)

    if len(result) > _MAX_OCR_CHARS:
        result = result[:_MAX_OCR_CHARS] + "..."

    return result.strip()


# ---- Private helpers ----

def _image_variants(image):
    """
    Generate multiple preprocessed variants of an image for multi-pass OCR.

    Different variants help catch text on colored overlays, popups, dark themes, etc.
    """
    from PIL import ImageEnhance, ImageOps

    # Pass 1: Original image
    yield image

    # Pass 2: Grayscale with enhanced contrast
    gray = image.convert("L")
    enhancer = ImageEnhance.Contrast(gray)
    high_contrast = enhancer.enhance(2.0)
    yield high_contrast

    # Pass 3: Inverted grayscale (catches white text on dark/red backgrounds)
    inverted = ImageOps.invert(gray)
    yield inverted

    # Pass 4: Binarized (pure black/white via threshold)
    binarized = gray.point(lambda x: 255 if x > 128 else 0, "1")
    yield binarized

def _resolve_lang(lang: str) -> str:
    """Check available Tesseract languages, fall back to eng if needed."""
    try:
        available = pytesseract.get_languages()
        if "rus" not in available and "rus" in lang:
            return "eng"
    except Exception:
        return "eng"
    return lang


def _load_image(source: str):
    """Try to load an image from file path, attachments folder, or base64."""
    # Try as direct file path
    if os.path.isfile(source):
        ext = os.path.splitext(source)[1].lower()
        if ext in IMAGE_EXTENSIONS:
            return Image.open(source)

    # Try resolving from input/attachments/
    resolved = resolve_image_path(source)
    if resolved and resolved != source:
        return Image.open(resolved)

    # Try as base64
    try:
        raw = source
        if raw.startswith("data:"):
            raw = raw.split(",", 1)[1]
        decoded = base64.b64decode(raw)
        return Image.open(io.BytesIO(decoded))
    except Exception:
        pass

    return None


def _detect_source_type(source: str) -> str:
    """Detect what kind of image source this is."""
    if os.path.isfile(source) or resolve_image_path(source):
        return "file"
    if source.startswith("data:") or len(source) > 200:
        return "base64"
    return "unknown"
