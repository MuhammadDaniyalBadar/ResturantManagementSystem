FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DEBUG=0
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN SECRET_KEY=build-only ALLOWED_HOSTS=build.local python manage.py collectstatic --noinput
# Data lives in Postgres (DATABASE_URL) and photos on a disk (MEDIA_ROOT). SEED_DEMO=1 adds the demo restaurant once.
CMD ["sh", "-c", "python manage.py migrate --noinput && if [ \"$SEED_DEMO\" = 1 ]; then python manage.py seed_demo --if-empty && python manage.py load_menu demo-kitchen; fi && exec daphne --proxy-headers -b 0.0.0.0 -p ${PORT:-8000} config.asgi:application"]
