import logging
import re

class PIIMaskingFormatter(logging.Formatter):
    """
    Custom formatter that intercepts log messages and masks sensitive PII
    (Phone numbers, Email addresses, and standard KZ IINs) before writing
    them to stdout or log files.
    """
    
    # Regex patterns for common PII
    PATTERNS = [
        # Phones: +7(123)456-78-90, 8 700 123 45 67, etc.
        (r'(?:\+?7|8)[\s\-]?\(?[0-9]{3}\)?[\s\-]?[0-9]{3}[\s\-]?[0-9]{2}[\s\-]?[0-9]{2}', '[REDACTED PHONE]'),
        
        # Emails
        (r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', '[REDACTED EMAIL]'),
        
        # IIN (12 digit identity number)
        (r'\b\d{12}\b', '[REDACTED IIN]'),
    ]

    def __init__(self, fmt=None, datefmt=None, style='%'):
        super().__init__(fmt, datefmt, style)
        self.compiled_patterns = [(re.compile(pattern), repl) for pattern, repl in self.PATTERNS]

    def format(self, record):
        # Format the original message
        original_message = super().format(record)
        
        # Mask PII patterns in the final string
        masked_message = original_message
        for pattern, replacement in self.compiled_patterns:
            masked_message = pattern.sub(replacement, masked_message)
            
        return masked_message

def get_safe_logger(name: str, level=logging.INFO) -> logging.Logger:
    """Returns a logger attached to the PII masking formatter."""
    logger = logging.getLogger(name)
    
    if not logger.handlers:
        logger.setLevel(level)
        
        handler = logging.StreamHandler()
        formatter = PIIMaskingFormatter(
            '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        
    return logger
