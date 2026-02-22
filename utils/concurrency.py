import threading

class ModelConcurrency:
    """Manages concurrency limits for local AI models based on optimization strategy.
    
    Implements Phase 3: Semaphore-Based Concurrency Control.
    Max 4 concurrent text jobs, Max 2 concurrent vision jobs.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(ModelConcurrency, cls).__new__(cls)
                # Limits from optim.md
                cls._instance.text_semaphore = threading.Semaphore(4)
                cls._instance.vision_semaphore = threading.Semaphore(2)
        return cls._instance

    @classmethod
    def get_text_semaphore(cls) -> threading.Semaphore:
        return cls().text_semaphore

    @classmethod
    def get_vision_semaphore(cls) -> threading.Semaphore:
        return cls().vision_semaphore
