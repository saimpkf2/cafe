import os
from pathlib import Path
import dj_database_url
from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
if not os.getenv('RAILWAY_ENVIRONMENT_ID'):
    load_dotenv(BASE_DIR / '.env', override=False)
DEBUG = os.getenv('DJANGO_DEBUG', '0') == '1'
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'development-only-change-before-deployment')
if not DEBUG and (len(SECRET_KEY) < 50 or SECRET_KEY.startswith(('development-', 'replace-'))):
    raise RuntimeError('Set DJANGO_SECRET_KEY for production')
ALLOWED_HOSTS = [x.strip() for x in os.getenv('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver' if DEBUG else '').split(',') if x.strip()]
if os.getenv('RAILWAY_ENVIRONMENT_ID'):
    ALLOWED_HOSTS.append('healthcheck.railway.app')
    if os.getenv('RAILWAY_PUBLIC_DOMAIN'):
        ALLOWED_HOSTS.append(os.environ['RAILWAY_PUBLIC_DOMAIN'])
CSRF_TRUSTED_ORIGINS = [x.strip() for x in os.getenv('DJANGO_CSRF_TRUSTED_ORIGINS', '').split(',') if x.strip()]
if DEBUG:
    CSRF_TRUSTED_ORIGINS = list(dict.fromkeys(CSRF_TRUSTED_ORIGINS + [
        'http://localhost:5173', 'http://127.0.0.1:5173',
    ]))
CSRF_FAILURE_VIEW = 'shop.views.csrf_failure'
INSTALLED_APPS = ['django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions', 'shop']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'django.middleware.clickjacking.XFrameOptionsMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware']
ROOT_URLCONF = 'cafe.urls'
WSGI_APPLICATION = 'cafe.wsgi.application'
database_url = os.getenv('DATABASE_URL')
if not database_url:
    raise ImproperlyConfigured('Set DATABASE_URL in backend/.env or the service environment. PostgreSQL is required.')
DATABASES = {'default': dj_database_url.parse(database_url, conn_max_age=60, conn_health_checks=True)}
if DATABASES['default']['ENGINE'] != 'django.db.backends.postgresql':
    raise ImproperlyConfigured('DATABASE_URL must use PostgreSQL; SQLite fallback is disabled.')
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
TIME_ZONE = 'Asia/Dubai'
USE_TZ = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_SSL_REDIRECT = not DEBUG
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
AUTH_PASSWORD_VALIDATORS = [{'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'}]

# Railway probes the container over HTTP; only this read-only route is exempt.
SECURE_REDIRECT_EXEMPT = [r'^api/health/$']
SECURE_HSTS_SECONDS = 0 if DEBUG else 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
DATABASES['default'].setdefault('OPTIONS', {}).setdefault('connect_timeout', 10)

