"""FIRE — Configuration module."""
import os
from dotenv import load_dotenv

load_dotenv(override=True)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/fire_db")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
