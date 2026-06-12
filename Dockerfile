# Imagen base liviana
FROM python:3.11-slim

# Configuraciones para logs inmediatos y limpieza
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Directorio de trabajo
WORKDIR /app

# Dependencias del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copiar e instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Crear directorios necesarios
RUN mkdir -p /app/data /app/logs /app/uploads

# Copiar código de la aplicación
COPY app.py .
COPY word_parser.py .
COPY gemini_service.py .
COPY templates/ ./templates/
COPY static/ ./static/

# Copiar archivos de configuración necesarios (si existen)
# NOTA: gc-key.json debe ser proporcionado como secreto o variable de entorno en producción
COPY gc-key.json* ./

# Exponer puerto (Cloud Run usa $PORT, pero por defecto 8080)
EXPOSE 8080

# Variables de entorno por defecto
ENV PORT=8080
ENV FLASK_ENV=production

# Healthcheck (usando curl que viene con la imagen base)
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/api/health')" || exit 1

# Ejecutar con Gunicorn
# Workers: 2 para balancear carga y recursos
# Threads: 4 por worker para manejar múltiples requests concurrentes
# Timeout: 120s para operaciones de IA que pueden tardar
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120 --access-logfile - --error-logfile - app:app"]