# Sistema de Gestión Clínica Pediátrica - Clínica

Sistema integral de gestión de pacientes pediátricos con procesamiento inteligente de documentos médicos, cálculo automático de triage con IA, y seguimiento completo del historial clínico.

## 🚀 Características Principales

### ✨ Funcionalidades con Inteligencia Artificial

- **🤖 Cálculo Automático de Triage**: Sistema de triage inteligente que analiza signos vitales y síntomas para determinar el nivel de urgencia (1-5)
- **📸 Procesamiento de Imágenes Médicas**: Extracción automática de datos desde formularios médicos escaneados usando Gemini Vision API
- **💡 Sugerencias Inteligentes**: 
  - Generación automática de recordatorios basados en datos del paciente
  - Detección automática de alertas urgentes
  - Resumen clínico inteligente generado por IA

### 📋 Gestión de Pacientes

- **Registro Completo**: Formulario pediátrico completo con todas las secciones (A-F)
- **Triage Médico**: Sistema de triage con validación profesional y auditoría
- **Anotaciones y Recordatorios**: 
  - Anotaciones urgentes por paciente
  - Recordatorios personalizados
  - Historia clínica editable
- **Indicadores Visuales**: Badges y alertas en la lista de pacientes para identificar casos urgentes

### 📊 Visualización y Análisis

- **Vista por Paciente**: Historial cronológico completo con reportes de enfermería e indicaciones médicas
- **Vista por Unidad**: Organización de pacientes por sala/unidad con indicadores de triage
- **Panel de Riesgo**: Identificación automática de pacientes críticos
- **Resumen Diario**: Actuación diaria destacada con última información relevante

---

## 🛠️ Tecnologías Utilizadas

- **Backend**: Flask 3.0.2
- **IA/ML**: Google Cloud Vertex AI (Gemini 2.0 Flash)
- **Procesamiento de Imágenes**: Pillow (PIL)
- **Procesamiento de Documentos**: python-docx
- **Frontend**: HTML5, CSS3 (Tailwind CSS), JavaScript (Vanilla)
- **Almacenamiento**: JSON (con sistema de backups automáticos)

---

## 📋 Requisitos del Sistema

### Requisitos Mínimos

- **Python**: 3.11 o superior (recomendado para compatibilidad con librerías de IA)
- **Sistema Operativo**: Windows, Linux o macOS
- **Memoria RAM**: Mínimo 2GB (recomendado 4GB+)
- **Espacio en Disco**: 500MB para la aplicación + espacio para datos

### Credenciales de Google Cloud

El sistema requiere credenciales de Google Cloud Platform para usar Vertex AI:
- Archivo `gc-key.json` con credenciales de servicio
- Proyecto con Vertex AI habilitado
- Permisos para usar Gemini API

---

## 🚀 Instalación y Configuración

### 1. Clonar o Descargar el Repositorio

```bash
git clone <repository-url>
cd clinica
```

### 2. Crear Entorno Virtual (Recomendado)

**Windows PowerShell:**
```powershell
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
```

**Windows CMD:**
```cmd
py -3.11 -m venv venv
venv\Scripts\activate.bat
```

**Linux/macOS:**
```bash
python3.11 -m venv venv
source venv/bin/activate
```

### 3. Instalar Dependencias

```bash
pip install -r requirements.txt
```

### 4. Configurar Credenciales de Google Cloud

1. Coloca tu archivo de credenciales `gc-key.json` en la raíz del proyecto
2. Asegúrate de que el archivo tenga los permisos necesarios para Vertex AI

### 5. Configurar Variables de Entorno (Opcional)

Crea un archivo `.env` en la raíz del proyecto:
```env
GOOGLE_CLOUD_PROJECT=tu-proyecto-id
PORT=5000
```

---

## 💻 Uso del Sistema

### Iniciar la Aplicación

```bash
python app.py
```

La aplicación estará disponible en `http://localhost:5000`

### Funcionalidades Principales

#### 1. Registro de Paciente con Triage

1. Click en **"Registrar Paciente"**
2. Completar datos básicos del paciente
3. Ingresar signos vitales y motivo de consulta
4. Click en **"Previsualizar Triage"** para cálculo automático con IA
5. Revisar y confirmar/modificar el nivel de triage sugerido
6. Guardar el registro

#### 2. Procesamiento de Imágenes Médicas

**Para Triage:**
1. Click en **"Subir Imagen"** → Seleccionar **"Ingreso con Triage"**
2. Subir imagen del formulario médico
3. El sistema extraerá automáticamente:
   - Datos del paciente
   - Signos vitales
   - Motivo de consulta
4. Revisar y confirmar los datos extraídos

**Para Ingreso Completo:**
1. Click en **"Subir Imagen"** → Seleccionar **"Ingreso Completo"**
2. Subir una o múltiples imágenes del formulario completo
3. El sistema extraerá todos los datos estructurados:
   - Sección A: Datos del paciente y padres
   - Sección B: Motivo de consulta y evolución
   - Sección C: Antecedentes
   - Sección D: Embarazo, parto, nutrición, desarrollo
   - Sección E: Antecedentes patológicos
   - Sección F: Datos socioeconómicos
   - Examen Físico completo (estructurado)

#### 3. Gestión de Anotaciones y Recordatorios

1. Seleccionar un paciente de la lista
2. En la sección de **Anotaciones y Recordatorios**:
   - Click en el icono de edición para agregar/modificar
   - Click en el icono de **IA** (✨) para generar sugerencias automáticas
3. Las sugerencias de IA incluyen:
   - Alertas urgentes detectadas automáticamente
   - Recordatorios relevantes basados en los datos
   - Resumen clínico profesional

#### 4. Vista por Unidad

1. Click en **"Vista por Unidad"**
2. Ver pacientes organizados por sala/unidad
3. Indicadores visuales muestran:
   - Nivel de triage por paciente
   - Pacientes con anotaciones urgentes o recordatorios
   - Score de riesgo

---

## 📂 Estructura del Proyecto

```
clinica/
├── app.py                      # Servidor Flask principal
├── gemini_service.py           # Servicio de IA (Gemini/Vertex AI)
├── word_parser.py              # Parser de documentos Word
├── requirements.txt            # Dependencias Python
├── gc-key.json                 # Credenciales Google Cloud (no incluir en git)
├── data/
│   └── residentes_db.json      # Base de datos principal
├── templates/
│   └── index.html              # Interfaz web completa
├── uploads/                    # Archivos temporales de imágenes
└── logs/                       # Logs de auditoría de triage
```

---

## 🔐 Seguridad y Privacidad

- **Credenciales**: El archivo `gc-key.json` NO debe subirse a repositorios públicos
- **Datos Sensibles**: Los datos de pacientes se almacenan localmente en JSON
- **Backups**: El sistema crea backups automáticos antes de guardar cambios
- **Auditoría**: Todas las modificaciones de triage se registran en logs

---

## 📊 Estructura de Datos

### Paciente Completo

```json
{
  "patient_id": "HC-12345",
  "patient": "Apellido, Nombre",
  "fecha_ingreso": "2026-03-03T13:06",
  "tipo_ingreso": "completo",
  "complete_admission_data": {
    "seccion_a": { /* Datos del paciente y padres */ },
    "seccion_b": { /* Motivo de consulta */ },
    "seccion_c": { /* Antecedentes */ },
    "seccion_d": { /* Embarazo, desarrollo */ },
    "seccion_e": { /* Antecedentes patológicos */ },
    "seccion_f": { /* Datos socioeconómicos */ },
    "examen_fisico": { /* Examen físico completo estructurado */ }
  },
  "annotations": {
    "urgent_notes": "Anotaciones urgentes...",
    "reminders": "Recordatorios...",
    "clinical_history": "Historia clínica..."
  },
  "triage_inicial": { /* Datos de triage */ },
  "shifts": [ /* Reportes de enfermería */ ],
  "shiftsPharmacy": [ /* Indicaciones médicas */ ]
}
```

---

## 🧪 API Endpoints Principales

### Pacientes

- `GET /api/patients/list` - Lista todos los pacientes
- `GET /api/reports/<patient_id>` - Obtiene reportes de un paciente
- `POST /api/patients/register` - Registra nuevo paciente con triage
- `POST /api/patients/register_complete` - Registra paciente con ingreso completo

### Procesamiento de Imágenes

- `POST /api/upload_image` - Procesa imagen para triage
- `POST /api/upload_image_complete` - Procesa imagen(es) para ingreso completo

### Anotaciones

- `GET /api/patients/<patient_id>/annotations` - Obtiene anotaciones
- `POST /api/patients/<patient_id>/annotations` - Actualiza anotaciones
- `POST /api/patients/<patient_id>/ai-suggestions` - Genera sugerencias de IA

### Triage

- `POST /api/calculate_triage` - Calcula nivel de triage con IA

---

## 🐛 Solución de Problemas

### Error: "Servicio de IA no disponible"

- Verifica que `gc-key.json` esté en la raíz del proyecto
- Confirma que las credenciales sean válidas
- Verifica que Vertex AI esté habilitado en tu proyecto de GCP

### Error al procesar imágenes

- Verifica que las imágenes sean legibles
- Asegúrate de que el tamaño total no exceda 100MB
- Formatos soportados: JPG, JPEG, PNG, GIF, BMP, WEBP

### Error: "Rate limit exceeded"

- El sistema tiene protección contra rate limiting
- Espera unos minutos y vuelve a intentar
- El sistema usará cálculo basado en reglas como respaldo

---

## 📝 Notas de Desarrollo

### Características de IA Implementadas

1. **Triage Inteligente**: Usa Gemini para analizar signos vitales y síntomas
2. **Extracción de Datos**: Gemini Vision API para leer formularios médicos
3. **Sugerencias Automáticas**: Análisis inteligente para generar recordatorios y alertas
4. **Resumen Clínico**: Generación automática de resúmenes profesionales

### Mejoras Futuras Sugeridas

- [ ] Integración con base de datos relacional (PostgreSQL)
- [ ] Autenticación de usuarios
- [ ] Exportación de reportes en PDF
- [ ] Notificaciones push para alertas urgentes
- [ ] Dashboard de estadísticas y métricas

---

## 📄 Licencia

Este proyecto es de uso interno para la clínica.

---

## 👥 Soporte

Para problemas o consultas, contactar al equipo de desarrollo.

---

## 🎯 Versión

**Versión Actual**: 2.0  
**Última Actualización**: Marzo 2026

---

## 🙏 Agradecimientos

- Google Cloud Platform / Vertex AI por el servicio de Gemini
- Comunidad de desarrolladores de código abierto
