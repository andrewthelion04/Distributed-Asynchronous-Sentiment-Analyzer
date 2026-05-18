import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# UI assets live outside the Django project (see /frontend at the repo root).
# In Docker the host's ./frontend is mounted at /frontend; locally we fall back
# to the repo-relative path so `manage.py runserver` works without env vars.
FRONTEND_DIR = Path(os.environ.get('FRONTEND_DIR', BASE_DIR.parent.parent / 'frontend'))

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'dev-fallback-key')

DEBUG = os.environ.get('DEBUG', 'False') == 'True'

ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'channels',
    'analyzer',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'sentiment_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [FRONTEND_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'sentiment_project.wsgi.application'
ASGI_APPLICATION = 'sentiment_project.asgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

_REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            'hosts': [(_REDIS_HOST, 6379)],
        },
    },
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Europe/Bucharest'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [FRONTEND_DIR / 'static']

# WhiteNoise: serve static files via Daphne without needing collectstatic.
# Without this, WhiteNoise only looks in STATIC_ROOT (post-collectstatic).
WHITENOISE_USE_FINDERS = True

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

RABBITMQ_HOST = os.environ.get('RABBITMQ_HOST', 'localhost')
RABBITMQ_USER = os.environ.get('RABBITMQ_USER', 'guest')
RABBITMQ_PASS = os.environ.get('RABBITMQ_PASS', 'guest')
RABBITMQ_QUEUE = 'live_chat_queue'

REDIS_HOST = _REDIS_HOST

GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')
