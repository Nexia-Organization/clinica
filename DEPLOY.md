# Guía de Despliegue - Sistema de Clínica

Esta guía explica cómo desplegar el sistema de gestión de pacientes y triage.

## Requisitos Previos

- Docker y Docker Compose instalados
- Archivo de credenciales de Google Cloud (`gc-key.json`) para usar Gemini AI
- Python 3.11+ (solo para desarrollo local sin Docker)

## Opción 1: Despliegue con Docker Compose (Recomendado para desarrollo)

1. **Preparar archivos necesarios:**
   ```bash
   # Asegúrate de tener el archivo gc-key.json en el directorio raíz
   ls gc-key.json
   ```

2. **Construir y ejecutar:**
   ```bash
   docker-compose up --build
   ```

3. **Acceder a la aplicación:**
   - Abre tu navegador en `http://localhost:8080`

4. **Ver logs:**
   ```bash
   docker-compose logs -f
   ```

5. **Detener el servicio:**
   ```bash
   docker-compose down
   ```

## Opción 2: Despliegue con Docker (Producción)

1. **Construir la imagen:**
   ```bash
   docker build -t clinica-app:latest .
   ```

2. **Ejecutar el contenedor:**
   ```bash
   docker run -d \
     --name clinica-app \
     -p 8080:8080 \
     -v $(pwd)/data:/app/data \
     -v $(pwd)/logs:/app/logs \
     -v $(pwd)/uploads:/app/uploads \
     -v $(pwd)/gc-key.json:/app/gc-key.json:ro \
     -e PORT=8080 \
     clinica-app:latest
   ```

## Opción 3: Despliegue en Google Cloud Run

1. **Autenticarse en Google Cloud:**
   ```bash
   gcloud auth login
   gcloud config set project YOUR_PROJECT_ID
   ```

2. **Construir y subir la imagen:**
   ```bash
   gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/clinica-app
   ```

3. **Desplegar en Cloud Run:**
   ```bash
   gcloud run deploy clinica-app \
     --image gcr.io/YOUR_PROJECT_ID/clinica-app \
     --platform managed \
     --region us-central1 \
     --allow-unauthenticated \
     --port 8080 \
     --memory 2Gi \
     --timeout 300 \
     --set-env-vars PORT=8080 \
     --set-secrets=GOOGLE_APPLICATION_CREDENTIALS_JSON=gc-key:latest
   ```

   **Nota:** Para usar el secreto de credenciales en Cloud Run:
   ```bash
   # Crear el secreto primero
   gcloud secrets create gc-key --data-file=gc-key.json
   
   # Luego desplegar con el secreto montado como archivo
   gcloud run deploy clinica-app \
     --image gcr.io/YOUR_PROJECT_ID/clinica-app \
     --platform managed \
     --region us-central1 \
     --allow-unauthenticated \
     --port 8080 \
     --memory 2Gi \
     --timeout 300 \
     --set-env-vars PORT=8080,GOOGLE_APPLICATION_CREDENTIALS=/secrets/gc-key.json \
     --set-secrets=/secrets/gc-key.json=gc-key:latest
   ```

## Opción 4: Despliegue en otros servicios (Heroku, Railway, etc.)

### Heroku

1. **Crear archivo `Procfile`:**
   ```
   web: gunicorn --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120 app:app
   ```

2. **Configurar variables de entorno:**
   ```bash
   heroku config:set PORT=8080
   heroku config:set GOOGLE_APPLICATION_CREDENTIALS_JSON="$(cat gc-key.json)"
   ```

3. **Desplegar:**
   ```bash
   git push heroku main
   ```

### Railway

1. Conecta tu repositorio a Railway
2. Configura las variables de entorno necesarias
3. Railway detectará automáticamente el Dockerfile y desplegará

## Estructura de Directorios Importante

```
clinica/
├── app.py                 # Aplicación Flask principal
├── word_parser.py          # Parser de documentos Word
├── gemini_service.py      # Servicio de Gemini AI
├── templates/             # Plantillas HTML
├── static/                # Archivos estáticos (JS, CSS)
├── data/                  # Datos de pacientes (JSON)
├── logs/                  # Logs de auditoría
├── uploads/               # Archivos subidos temporalmente
├── gc-key.json            # Credenciales de Google Cloud (NO subir a Git)
└── requirements.txt       # Dependencias Python
```

## Variables de Entorno

Copia `.env.example` a `.env` y configura según tu entorno:

```bash
cp .env.example .env
```

## Seguridad

⚠️ **IMPORTANTE:**
- **NUNCA** subas `gc-key.json` a repositorios públicos
- Usa secretos/variables de entorno en producción
- El archivo `.dockerignore` ya excluye archivos sensibles

## Verificación Post-Despliegue

1. **Health Check:**
   ```bash
   curl http://localhost:8080/api/health
   ```

2. **Verificar logs:**
   ```bash
   docker logs clinica-app
   ```

3. **Probar funcionalidades:**
   - Acceder a la interfaz web
   - Registrar un paciente
   - Subir un documento Word
   - Verificar cálculo de triage

## Troubleshooting

### Error: "No se encontró el archivo de credenciales"
- Asegúrate de que `gc-key.json` existe y está montado correctamente
- Verifica los permisos del archivo

### Error: "Resource exhausted" (Rate Limit de Gemini)
- El sistema tiene retry automático con backoff exponencial
- Considera aumentar el timeout en Gunicorn si es necesario

### Error: "Port already in use"
- Cambia el puerto en `docker-compose.yml` o usa `-p` con otro puerto

### Los datos no persisten
- Verifica que los volúmenes estén montados correctamente
- Revisa los permisos de escritura en `data/`, `logs/`, `uploads/`

## Monitoreo

Los logs de triage se guardan en `logs/triage_audit_YYYYMM.log`

Para ver logs en tiempo real:
```bash
docker logs -f clinica-app
```

## Backup

Los datos se guardan en `data/residentes_db.json`. Se crean backups automáticos con extensión `.backup`.

Para hacer backup manual:
```bash
docker exec clinica-app cp /app/data/residentes_db.json /app/data/residentes_db.json.backup.$(date +%Y%m%d)
```
