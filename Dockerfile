FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DJANGO_DEBUG=0
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home app
COPY --chown=app:app . .
USER app
CMD ["gunicorn", "--config", "gunicorn.conf.py", "cafe.wsgi:application"]
