import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-chave-insegura")
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "*").split(",")

# Projeto propositalmente enxuto: sem admin, auth nem banco relacional.
# Todo o armazenamento é feito no Redis.
INSTALLED_APPS = ["documentos"]
MIDDLEWARE = ["django.middleware.common.CommonMiddleware"]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
DATABASES = {}

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

# Redis
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
RESUMO_TTL_SEGUNDOS = int(os.getenv("RESUMO_TTL_SEGUNDOS", "60"))
RESUMO_ATRASO_SEGUNDOS = float(os.getenv("RESUMO_ATRASO_SEGUNDOS", "1.5"))
