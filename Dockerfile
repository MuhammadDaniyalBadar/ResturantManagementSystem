FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DEBUG=0
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN SECRET_KEY=build-only python manage.py collectstatic --noinput
# Alpha: SQLite lives inside the container, so data resets on every deploy and the demo data is recreated.
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py seed_demo --if-empty && exec daphne --proxy-headers -b 0.0.0.0 -p ${PORT:-8000} config.asgi:application"]
