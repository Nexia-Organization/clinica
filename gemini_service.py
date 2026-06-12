import os
import vertexai
import json
import time
from vertexai.generative_models import GenerativeModel, GenerationConfig
from google.oauth2 import service_account

from PIL import Image, ImageOps, ImageEnhance

def preprocess_image(input_path: str, max_side: int = 2000):
    """
    Preprocesa la imagen para mejorar lectura de formularios médicos
    """
    img = Image.open(input_path)

    # corregir rotación EXIF
    img = ImageOps.exif_transpose(img)

    # convertir a RGB
    img = img.convert("RGB")

    # mejorar contraste
    img = ImageOps.autocontrast(img)

    # mejorar nitidez
    img = ImageEnhance.Sharpness(img).enhance(1.4)

    # redimensionar si es muy grande
    w, h = img.size
    scale = max_side / max(w, h)

    if scale < 1:
        img = img.resize((int(w * scale), int(h * scale)))

    output_path = input_path + "_processed.png"
    img.save(output_path, format="PNG")

    return output_path

class GeminiService:
    def __init__(self, credentials_path: str = "gc-key.json", project_id: str = None, location: str = "us-central1"):
        """
        Inicializa el servicio de Gemini usando un archivo de credenciales JSON.
        
        Args:
            credentials_path: Ruta al archivo JSON de credenciales de Google Cloud
            project_id: ID del proyecto (se obtiene del JSON si no se proporciona)
            location: Región de Vertex AI (default: us-central1)
        """
        self.credentials_path = credentials_path
        self.location = location
        
        # Cargar credenciales desde el archivo JSON
        if not os.path.exists(credentials_path):
            raise FileNotFoundError(f"No se encontró el archivo de credenciales: {credentials_path}")
        
        # Cargar el JSON para obtener el project_id si no se proporciona
        with open(credentials_path, 'r', encoding='utf-8') as f:
            credentials_data = json.load(f)
        
        # Obtener project_id del JSON o del parámetro
        self.project_id = project_id or credentials_data.get('project_id') or os.getenv("GOOGLE_CLOUD_PROJECT")
        
        if not self.project_id:
            raise ValueError("No se pudo determinar el project_id. Proporciónelo como parámetro o inclúyalo en el archivo JSON.")
        
        # Inicializar Vertex AI con las credenciales
        credentials = service_account.Credentials.from_service_account_file(
            credentials_path,
            scopes=['https://www.googleapis.com/auth/cloud-platform']
        )
        
        vertexai.init(project=self.project_id, location=self.location, credentials=credentials)
        self.model = GenerativeModel("gemini-2.0-flash-001")

    def calculate_triage(self, vitals: dict, note: str, edad: str = None, embarazo: bool = False) -> dict:
        """
        Calcula el nivel de triage usando IA (Gemini) según las reglas institucionales.
        
        Args:
            vitals: Diccionario con signos vitales (ta_sis, ta_dia, sat, fc, fr, temp_c, glasgow)
            note: Nota clÃ­nica con motivo de consulta y observaciones
            edad: Edad del paciente (opcional)
            embarazo: Si el paciente está embarazado (opcional)
        
        Returns:
            Diccionario con level, label, time_max, reasons, color
        """
        
        # Construir prompt con las reglas de triage
        triage_rules = """
TRIAGE 1 - ATENCIÓN INMEDIATA (Tiempo máximo: Inmediato)
Criterios:
- Glasgow < 8 (coma profundo)
- Saturación O2 < 85% (hipoxia severa)
- Tensión arterial sistólica < 70 mmHg (hipotensión severa)
- SÃ­ntomas crÃ­ticos: paro cardiorrespiratorio, apnea, convulsión activa, estatus convulsivo, asfixia, hemorragia severa
- Compromiso de vÃ­a aérea
- Shock

TRIAGE 2 - ALTA PRIORIDAD (Tiempo máximo: 10 minutos)
Criterios:
- Glasgow 8-12 (estupor/coma superficial)
- Saturación O2 85-89% (hipoxia moderada)
- Tensión arterial sistólica < 90 mmHg o > 180 mmHg (alteración tensional severa)
- Temperatura â‰¥ 39.0Â°C (fiebre alta)
- SÃ­ntomas urgentes: dolor severo/intenso, disnea, dificultad respiratoria, compromiso de conciencia
- Signos de descompensación hemodinámica

TRIAGE 3 - PRIORIDAD MEDIA (Tiempo máximo: 30 minutos)
Criterios:
- Glasgow 13-14 (obnubilación leve)
- Saturación O2 90-93% (saturación baja)
- Tensión arterial sistólica 90-99 mmHg o 161-180 mmHg (alteración tensional moderada)
- Temperatura 38.0-38.9Â°C (fiebre moderada)
- SÃ­ntomas moderados: caÃ­da, golpe, herida, vómito, dolor moderado
- Alteraciones que requieren evaluación pero no son crÃ­ticas

TRIAGE 4 - PRIORIDAD BAJA (Tiempo máximo: 60 minutos)
Criterios:
- Temperatura 37.5-37.9Â°C (febrÃ­cula)
- SÃ­ntomas leves: molestia, incomodidad, consulta no urgente
- Condiciones estables que pueden esperar evaluación

TRIAGE 5 - RUTINA (Tiempo máximo: 120 minutos)
Criterios:
- Estado estable
- Signos vitales normales
- Consulta de rutina o control
- Sin sÃ­ntomas agudos

INSTRUCCIONES:
1. Evalúa TODOS los signos vitales proporcionados
2. Considera el motivo de consulta y observaciones en la nota
3. Si hay múltiples criterios, usa el nivel MÃS ALTO (más urgente)
4. Si no hay datos suficientes pero hay sÃ­ntomas crÃ­ticos en la nota, prioriza esos sÃ­ntomas
5. Si todos los signos vitales son normales y no hay sÃ­ntomas, asigna TRIAGE 5
6. Considera la edad del paciente si es relevante (niÃ±os vs adultos tienen diferentes rangos normales)
7. Si hay embarazo, considera que puede haber alteraciones fisiológicas normales

RESPONDE ÃšNICAMENTE CON UN JSON en este formato exacto:
{
  "level": 1-5,
  "label": "Nombre del nivel",
  "time_max": "Tiempo máximo de atención",
  "reasons": ["Razón 1", "Razón 2", ...],
  "color": "red|orange|yellow|lightblue|green"
}
"""
        
        # Construir datos del paciente para el prompt
        vitals_str = []
        if vitals.get("ta_sis") is not None:
            vitals_str.append(f"Tensión arterial sistólica: {vitals.get('ta_sis')} mmHg")
        if vitals.get("ta_dia") is not None:
            vitals_str.append(f"Tensión arterial diastólica: {vitals.get('ta_dia')} mmHg")
        if vitals.get("sat") is not None:
            vitals_str.append(f"Saturación O2: {vitals.get('sat')}%")
        if vitals.get("fc") is not None:
            vitals_str.append(f"Frecuencia cardÃ­aca: {vitals.get('fc')} bpm")
        if vitals.get("fr") is not None:
            vitals_str.append(f"Frecuencia respiratoria: {vitals.get('fr')} rpm")
        if vitals.get("temp_c") is not None:
            vitals_str.append(f"Temperatura: {vitals.get('temp_c')}Â°C")
        if vitals.get("glasgow") is not None:
            vitals_str.append(f"Escala de Glasgow: {vitals.get('glasgow')}/15")
        
        paciente_info = []
        if edad:
            paciente_info.append(f"Edad: {edad}")
        if embarazo:
            paciente_info.append("Embarazo: SÃ­")
        
        prompt = f"""
{triage_rules}

DATOS DEL PACIENTE:
{chr(10).join(paciente_info) if paciente_info else "No especificada"}

SIGNOS VITALES:
{chr(10).join(vitals_str) if vitals_str else "No disponibles"}

NOTA CLÃNICA:
{note if note else "Sin observaciones"}

Evalúa el triage según las reglas proporcionadas y responde SOLO con el JSON solicitado.
"""
        
        # Reintentos con backoff exponencial para manejar rate limiting
        max_retries = 3
        base_delay = 1  # segundos
        
        for attempt in range(max_retries):
            try:
                if attempt == 0:
                    print(f"[Gemini] Calculando triage con IA...")
                
                generation_config = GenerationConfig(
                    temperature=0.1,  # Baja temperatura para respuestas más consistentes
                    max_output_tokens=500,
                    response_mime_type="application/json"
                )
                
                response = self.model.generate_content(
                    prompt,
                    generation_config=generation_config
                )
                
                print(f"[Gemini] âœ“ Triage calculado exitosamente (nivel: {json.loads(response.text.strip()).get('level', 'N/A')})")
                
                # Parsear respuesta JSON
                result_text = response.text.strip()
                
                # Limpiar si tiene markdown code blocks
                if "```json" in result_text:
                    result_text = result_text.split("```json")[1].split("```")[0].strip()
                elif "```" in result_text:
                    result_text = result_text.split("```")[1].split("```")[0].strip()
                
                triage_result = json.loads(result_text)
                
                # Validar estructura
                if not isinstance(triage_result.get("level"), int) or not (1 <= triage_result.get("level") <= 5):
                    raise ValueError("Nivel de triage inválido")
                
                # Asegurar que tenga todos los campos requeridos
                default_labels = {
                    1: "Atención Inmediata",
                    2: "Alta Prioridad",
                    3: "Prioridad Media",
                    4: "Prioridad Baja",
                    5: "Rutina"
                }
                
                default_times = {
                    1: "Inmediato",
                    2: "10 minutos",
                    3: "30 minutos",
                    4: "60 minutos",
                    5: "120 minutos"
                }
                
                default_colors = {
                    1: "red",
                    2: "orange",
                    3: "yellow",
                    4: "lightblue",
                    5: "green"
                }
                
                return {
                    "level": triage_result.get("level", 5),
                    "label": triage_result.get("label", default_labels.get(triage_result.get("level", 5), "Rutina")),
                    "time_max": triage_result.get("time_max", default_times.get(triage_result.get("level", 5), "120 minutos")),
                    "reasons": triage_result.get("reasons", ["Evaluación clÃ­nica"]),
                    "color": triage_result.get("color", default_colors.get(triage_result.get("level", 5), "green"))
                }
                
            except Exception as e:
                error_str = str(e)
                
                # Detectar error 429 (rate limiting)
                if "429" in error_str or "Resource exhausted" in error_str:
                    if attempt < max_retries - 1:
                        # Calcular delay exponencial: 1s, 2s, 4s
                        delay = base_delay * (2 ** attempt)
                        print(f"[Gemini] âš  Rate limit alcanzado. Reintentando en {delay} segundos... (intento {attempt + 1}/{max_retries})")
                        time.sleep(delay)
                        continue
                    else:
                        # Ãšltimo intento falló, usar fallback
                        print(f"[Gemini] âœ— Rate limit persistente después de {max_retries} intentos. Usando cálculo de respaldo.")
                        raise Exception("Rate limit excedido")
                else:
                    # Otro tipo de error
                    print(f"[Gemini] âœ— Error: {error_str[:100]}")
                    raise
        
        # Si llegamos aquÃ­, todos los reintentos fallaron
        raise Exception("Error después de múltiples intentos")
    
    def extract_patient_data_from_image(self, image_path: str) -> dict:
        """
        Extrae datos del paciente desde una imagen de ingreso médico usando Gemini Vision.
        
        Args:
            image_path: Ruta al archivo de imagen
            
        Returns:
            Diccionario con los datos extraÃ­dos del paciente
        """
        import base64
        
        # Leer imagen y codificar en base64
        with open(image_path, 'rb') as image_file:
            image_data = base64.b64encode(image_file.read()).decode('utf-8')
        
        # Determinar tipo MIME
        ext = image_path.lower().split('.')[-1]
        mime_types = {
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'png': 'image/png',
            'gif': 'image/gif',
            'bmp': 'image/bmp',
            'webp': 'image/webp'
        }
        mime_type = mime_types.get(ext, 'image/jpeg')
        
        prompt = """
Analiza esta imagen de una FICHA DE IDENTIFICACIÓN DEL PACIENTE (formulario de ingreso médico mexicano) y extrae TODA la información disponible.

Este formulario tÃ­picamente contiene:
- NOMBRE con campos separados: APELLIDO PATERNO, APELLIDO MATERNO, NOMBRE(S)
- FECHA DE NACIMIENTO (dÃ­a, mes, aÃ±o) - CALCULA LA EDAD si es posible
- SEXO (HOMBRE/MUJER)
- CURP (Clave Ãšnica de Registro de Población) - equivalente al DNI
- ESTADO CIVIL, OCUPACIÓN
- TIPO DE SANGRE, ALERGIAS
- DOMICILIO, TELÃ‰FONOS
- NOMBRE DEL FAMILIAR RESPONSABLE

Mapea los datos del formulario a estos campos del sistema:

CAMPO: nombre
- Busca en: "NOMBRE(S)" o "NOMBRE"
- Si hay apellidos separados, usa solo el nombre de pila

CAMPO: apellido  
- Busca en: "APELLIDO PATERNO" y "APELLIDO MATERNO"
- Combina ambos: "ApellidoPaterno ApellidoMaterno" o solo "ApellidoPaterno" si no hay materno

CAMPO: dni
- Busca en: "CURP" (Clave Ãšnica de Registro de Población)
- También busca otros números de identificación si CURP no está disponible

CAMPO: edad
- Calcula desde "FECHA DE NACIMIENTO" si está disponible
- Formato: "XX aÃ±os" o "XX aÃ±os, Y meses"
- Si no puedes calcular, busca campos que digan "EDAD" directamente

CAMPO: historia_clinica
- Busca números de expediente, historia clÃ­nica, o identificadores del hospital

CAMPO: sala
- Busca campos como "SALA", "UNIDAD", "ÃREA", "SERVICIO"

CAMPO: cama
- Busca "CAMA", "NÃšMERO DE CAMA"

CAMPO: diagnostico_principal
- Busca "DIAGNÓSTICO", "DIAGNÓSTICO PRINCIPAL", "MOTIVO DE INGRESO"

CAMPO: antecedentes
- Busca "ANTECEDENTES", "ALERGIAS", información médica previa
- Incluye tipo de sangre si está disponible: "Tipo de sangre: X" o "Grupo sanguÃ­neo: X"

CAMPO: tipo_clasificacion
- Si es un formulario de identificación nuevo, probablemente es "Ingreso"
- Busca indicadores de "INGRESO", "CONTROL", "PASE", etc.

CAMPO: motivo_consulta
- Busca "MOTIVO DE CONSULTA", "MOTIVO DE INGRESO", "RAZÓN DE INGRESO"

CAMPO: ta_sis, ta_dia, sat, fc, fr, temp_c, glasgow
- Busca signos vitales si están en el formulario
- Formato común: "TA: 120/80", "SAT: 95%", "FC: 80", "FR: 20", "TEMP: 37Â°C"

CAMPO: nivel_conciencia, dolor_escala
- Busca escalas de Glasgow, nivel de conciencia, escala de dolor si están presentes

CAMPO: observaciones
- Incluye cualquier información adicional relevante:
  - Ocupación
  - Estado civil
  - Lengua que habla
  - Domicilio completo
  - Teléfonos de contacto
  - Nombre del familiar responsable
  - Cualquier nota médica adicional

CAMPO: embarazo
- true si SEXO es "MUJER" y hay indicios de embarazo en el formulario
- false en otros casos o null si no es aplicable

INSTRUCCIONES ESPECÃFICAS:
1. Si el formulario tiene "APELLIDO PATERNO" y "APELLIDO MATERNO" separados, combÃ­nalos en el campo "apellido"
2. El CURP es el equivalente mexicano al DNI - úsalo para el campo "dni"
3. Calcula la edad desde la fecha de nacimiento si está disponible
4. Si un campo no está presente, devuélvelo como null
5. Los números deben ser números (no strings), excepto edad que debe ser string como "45 aÃ±os"
6. Los valores booleanos deben ser true/false
7. Si hay múltiples formularios o pacientes en la imagen, extrae solo el primero o el más completo

Responde ÃšNICAMENTE con un JSON válido en este formato exacto:
{
  "nombre": "string o null",
  "apellido": "string o null",
  "dni": "string o null",
  "edad": "string o null",
  "historia_clinica": "string o null",
  "sala": "string o null",
  "cama": "string o null",
  "diagnostico_principal": "string o null",
  "antecedentes": "string o null",
  "tipo_clasificacion": "string o null",
  "motivo_consulta": "string o null",
  "ta_sis": número o null,
  "ta_dia": número o null,
  "sat": número o null,
  "fc": número o null,
  "fr": número o null,
  "temp_c": número o null,
  "glasgow": número o null,
  "nivel_conciencia": "string o null",
  "dolor_escala": "string o null",
  "observaciones": "string o null",
  "embarazo": boolean o null
}
"""
        
        # Reintentos con backoff exponencial
        max_retries = 3
        base_delay = 1
        
        for attempt in range(max_retries):
            try:
                if attempt == 0:
                    print(f"[Gemini Vision] Analizando imagen para extraer datos del paciente...")
                
                generation_config = GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=2000,
                    response_mime_type="application/json"
                )
                
                # Crear contenido con imagen y prompt
                from vertexai.generative_models import Part
                image_part = Part.from_data(
                    data=base64.b64decode(image_data),
                    mime_type=mime_type
                )
                
                response = self.model.generate_content(
                    [image_part, prompt],
                    generation_config=generation_config
                )
                
                print(f"[Gemini Vision] âœ“ Datos extraÃ­dos exitosamente")
                
                # Parsear respuesta JSON
                result_text = response.text.strip()
                
                # Limpiar si tiene markdown code blocks
                if "```json" in result_text:
                    result_text = result_text.split("```json")[1].split("```")[0].strip()
                elif "```" in result_text:
                    result_text = result_text.split("```")[1].split("```")[0].strip()
                
                patient_data = json.loads(result_text)
                
                return patient_data

            except Exception as e:
                error_str = str(e)
                
                # Detectar error 429 (rate limiting)
                if "429" in error_str or "Resource exhausted" in error_str:
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        print(f"[Gemini Vision] âš  Rate limit alcanzado. Reintentando en {delay} segundos... (intento {attempt + 1}/{max_retries})")
                        time.sleep(delay)
                        continue
                    else:
                        print(f"[Gemini Vision] âœ— Rate limit persistente después de {max_retries} intentos.")
                        raise Exception("Rate limit excedido al procesar imagen")
                else:
                    print(f"[Gemini Vision] âœ— Error: {error_str[:200]}")
                    raise
        
        raise Exception("Error después de múltiples intentos al procesar imagen")
    
    def extract_complete_admission_data_from_image(self, image_path: str) -> dict:
        """
        Extrae datos completos del formulario de ingreso pediátrico desde una imagen usando Gemini Vision.
        """
        import base64
        import json
        import time
        import os
        from vertexai.generative_models import Part
        from vertexai.generative_models import GenerationConfig

        # 1) PREPROCESAR (misma función segura que te pasé antes)
        processed_path = preprocess_image(image_path, max_side=2000)

        # 2) Leer imagen procesada
        with open(processed_path, "rb") as f:
            img_bytes = f.read()

        # MIME
        ext = processed_path.lower().split(".")[-1]
        mime_type = "image/png" if ext == "png" else "image/jpeg"

        image_part = Part.from_data(data=img_bytes, mime_type=mime_type)

        # 3) PROMPT EN UTF-8 (SIN "CRÃ…")
        prompt = """
CRÍTICO: Este formulario contiene ESCRITURA A MANO de médicos sobre un FORMULARIO MÉDICO IMPRESO.

    REGLAS FUNDAMENTALES:
    1) NO INVENTES DATOS: si no puedes leer algo con certeza, devuélvelo como null.
    2) IDENTIFICA CAMPOS ILEGIBLES: reporta la ruta completa en "campos_ilegibles".
    3) INTERPRETA CON PRECAUCIÓN: solo si el contexto médico lo hace razonablemente seguro.
    4) PRESERVA LO LEGIBLE: si es parcial, extrae solo lo legible.

PRIMER PASO OBLIGATORIO:
    1) Identifica los campos IMPRESOS del formulario.
    2) Lee los valores MANUSCRITOS del médico.
    3) Mapea los valores al JSON.

    UNIDADES (si es claro):
    m, cm, kg, g, mmHg, °C

    SEXO:
    M = masculino, F = femenino (si es claro: devolver "masculino"/"femenino")

    CHECKBOXES:
    true solo si ves una marca clara (X, ✓, ✔), false si está claramente vacío, null si no puedes determinar.

Responde ÚNICAMENTE con un JSON válido en este formato exacto:
{
  "campos_ilegibles": [
    "ruta.del.campo.que.no.pudiste.leer",
    "ejemplo: seccion_a.apellido_nombre_nino",
    "ejemplo: examen_fisico.peso"
  ],
  "seccion_a": {
    "apellido_nombre_nino": "string o null",
    "sexo": "string o null",
    "fecha_nacimiento": "string o null (formato DD/MM/AAAA)",
    "apellido_nombre_madre": "string o null",
    "edad_madre": "string o null",
    "apellido_nombre_padre": "string o null",
    "edad_padre": "string o null",
    "numero_afiliado": "string o null",
    "mutual": "string o null",
    "telefono": "string o null",
    "domicilio": "string o null",
    "medico_caso": "string o null",
    "historia_clinica": "string o null",
    "cuidados_intermedios": "string o null"
  },
  "seccion_b": {
    "motivo_consulta": "string o null",
    "fecha_comienzo": "string o null",
    "evolucion": "string o null",
    "tratamientos_instituidos": "string o null"
  },
  "seccion_c": {
    "antecedentes_hereditarios_familiares": "string o null",
    "tbc": "boolean o null",
    "chagas": "boolean o null",
    "alergia": "boolean o null",
    "sifilis": "boolean o null",
    "diabetes": "boolean o null",
    "enf_sangre": "boolean o null",
    "enf_neurologicas": "boolean o null"
  },
  "seccion_d": {
    "embarazo_parto": {
      "evolucion_embarazo": "string o null",
      "drogas": "string o null",
      "rx": "string o null",
      "ruptura_bolsa": "string o null",
      "peso_nacimiento": "string o null",
      "reanimacion": "boolean o null",
      "cianosis": "boolean o null",
      "ictericia": "boolean o null",
      "convulsiones": "boolean o null",
      "otras": "string o null"
    },
    "nutricion": "string o null",
    "antecedentes_psicomotores": {
      "sonrisa_social": "string o null",
      "sosten_cabeza": "string o null",
      "sentado": "string o null",
      "camina": "string o null",
      "palabras": "string o null",
      "frases": "string o null",
      "control_esfinter": "string o null",
      "escuela_grado": "string o null",
      "escuela_problemas": "string o null"
    },
    "inmunizaciones": "string o null"
  },
  "seccion_e": {
    "antecedentes_patologicos": "string o null"
  },
  "seccion_f": {
    "datos_socioeconomicos": "string o null"
  },
  "examen_fisico": {
    "fecha": "string o null (formato DD/MM/AAAA)",
    "hora": "string o null",
    "edad": "string o null",
    "peso": "string o null",
    "talla": "string o null",
    "perimetro_cefalico": "string o null",
    "temp_rectal": "string o null",
    "temp_axilar": "string o null",
    "aspecto_general": "string o null",
    "psiquismo": {
      "normal": "boolean o null",
      "inquietud": "boolean o null",
      "delirio": "boolean o null",
      "obnubilado": "boolean o null",
      "estupor": "boolean o null",
      "coma": "boolean o null",
      "grado": "string o null"
    },
    "decubito": {
      "dorsal": "boolean o null",
      "ventral": "boolean o null",
      "indiferente": "boolean o null",
      "activo": "boolean o null",
      "pasivo": "boolean o null",
      "obligado": "boolean o null"
    },
    "piel": {
      "cianosis": "boolean o null",
      "palidez": "boolean o null",
      "ictericia": "boolean o null",
      "signo_pliegue": "boolean o null",
      "humedad": "boolean o null",
      "turgor": "boolean o null",
      "elasticidad": "boolean o null",
      "otros": "string o null"
    },
    "tcs": {
      "paniculo_adiposo": {
        "conservado": "boolean o null",
        "disminuido": "boolean o null",
        "aumentado": "boolean o null"
      },
      "edemas": "string o null",
      "ganglios": "string o null"
    },
    "cabeza": {
      "craneo": {
        "forma": "string o null",
        "facies": "string o null"
      },
      "fontanela": {
        "tamano": "string o null",
        "tension": "string o null"
      },
      "ojos": {
        "enoftalmos": "boolean o null",
        "exoftalmos": "boolean o null",
        "pupilas_miosis": "boolean o null",
        "pupilas_midriasis": "boolean o null",
        "otras": "string o null",
        "rfm": "boolean o null",
        "estrabismo": "boolean o null",
        "conjuntivas": "string o null"
      },
      "oidos": {
        "pabellones": "string o null",
        "p_trago": "boolean o null",
        "otoscopia": "string o null"
      },
      "boca": "string o null",
      "nariz": "string o null",
      "cuello": "string o null"
    },
    "torax": {
      "inspeccion": {
        "forma": "string o null",
        "fr": "string o null",
        "disnea_tipo": "string o null",
        "trajes_sc": "boolean o null",
        "ic": "boolean o null",
        "sciav": "boolean o null",
        "rosario_costal": "boolean o null"
      },
      "palpacion": "string o null",
      "percusion": {
        "normal": "boolean o null",
        "submale": "boolean o null",
        "mate": "boolean o null",
        "timpanica": "boolean o null"
      },
      "auscultacion": "string o null"
    },
    "cardiovascular": {
      "inspeccion": "string o null",
      "palpacion_pulsos": {
        "radial": "boolean o null",
        "femoral": "boolean o null",
        "pedios": "boolean o null",
        "fremitos": "boolean o null"
      },
      "auscultacion": "string o null",
      "fc": "string o null",
      "ta": "string o null",
      "normal": "boolean o null",
      "soplo": "boolean o null",
      "arritmia": "boolean o null"
    },
    "abdomen": {
      "inspeccion": {
        "simetrico": "boolean o null",
        "distendido": "boolean o null",
        "excavado": "boolean o null",
        "circulacion_colat": "boolean o null",
        "hernia_umbilical": "boolean o null"
      },
      "palpacion": {
        "bid": "boolean o null",
        "dolor_sup": "boolean o null",
        "profundo": "boolean o null",
        "defensa": "boolean o null",
        "contractura": "boolean o null"
      },
      "percusion": "string o null",
      "auscultacion": "string o null"
    },
    "higado": {
      "borde_superior": "string o null",
      "borde_inferior": "string o null",
      "superficie": "string o null",
      "consistencia": "string o null"
    },
    "bazo": {
      "palpable": "boolean o null",
      "tamano": "string o null",
      "consistencia": "string o null"
    },
    "rinon": {
      "palpable": "boolean o null",
      "cual": "string o null",
      "peloteo": "string o null"
    },
    "genitales": {
      "masculino": "string o null",
      "femenino": "string o null"
    },
    "ano": {
      "normal": "boolean o null",
      "anomalias": "boolean o null",
      "prolapso": "boolean o null",
      "fisuras": "boolean o null"
    },
    "soma": {
      "raquis": {
        "normal": "boolean o null",
        "cifosis": "boolean o null",
        "lordosis": "boolean o null",
        "escoliosis": "boolean o null"
      },
      "extremidades": {
        "normal": "boolean o null",
        "deformidades": "boolean o null",
        "anomalias": "boolean o null",
        "dolor": "boolean o null",
        "articulaciones": "string o null",
        "desarrollo_muscular": "string o null"
      }
    },
    "sistema_nervioso": {
      "paralisis": "boolean o null",
      "paresias": "boolean o null",
      "rigidez": {
        "nuca": "boolean o null",
        "columna": "boolean o null",
        "miembros": "boolean o null",
        "kerning": "boolean o null",
        "brudzinsky": "boolean o null"
      },
      "reflejos": {
        "patelar": "boolean o null",
        "aquilano": "boolean o null",
        "babinsky": "boolean o null",
        "enderezamiento": "boolean o null",
        "marcha": "boolean o null",
        "succion": "boolean o null",
        "soslen": "boolean o null",
        "presion_palmar": "boolean o null"
      },
      "movimientos_involuntarios": "boolean o null",
      "convulsiones": "boolean o null",
      "locomocion": "string o null",
      "fuerza_muscular": "string o null",
      "observaciones": "string o null"
    }
  },
  "presuncion_diagnostico": "string o null",
  "plan_estudio_tratamiento": "string o null"
}

    """.strip()

        # 4) Config extractor: temp 0.0
        generation_config = GenerationConfig(
            temperature=0.0,
            max_output_tokens=5000,
            response_mime_type="application/json",
        )

        max_retries = 3
        base_delay = 1

        try:
            for attempt in range(max_retries):
                try:
                    # 5) PROMPT PRIMERO, IMAGEN DESPUÉS
                    response = self.model.generate_content(
                        [prompt, image_part],
                        generation_config=generation_config,
                    )
                    
                    result_text = response.text.strip()

                    if "```json" in result_text:
                        result_text = result_text.split("```json")[1].split("```")[0].strip()
                    elif "```" in result_text:
                        result_text = result_text.split("```")[1].split("```")[0].strip()
                    
                    return json.loads(result_text)

                except Exception as e:
                    if ("429" in str(e)) or ("Resource exhausted" in str(e)):
                        if attempt < max_retries - 1:
                            time.sleep(base_delay * (2 ** attempt))
                            continue
                    raise
        finally:
            # Limpieza del archivo procesado
            if processed_path != image_path and os.path.exists(processed_path):
                try:
                    os.remove(processed_path)
                except:
                    pass

    def generate_smart_reminders(self, patient_data: dict) -> list:
        """
        Genera sugerencias inteligentes de recordatorios basadas en los datos del paciente usando IA.
        
        Args:
            patient_data: Diccionario con datos del paciente (seccion_a, examen_fisico, etc.)
        
        Returns:
            Lista de strings con sugerencias de recordatorios
        """
        try:
            prompt = f"""Eres un asistente médico experto. Analiza los siguientes datos de un paciente pediátrico y genera sugerencias de recordatorios importantes para el equipo médico.

Datos del paciente:
- Nombre: {patient_data.get('seccion_a', {}).get('apellido_nombre_nino', 'N/A')}
- Edad madre: {patient_data.get('seccion_a', {}).get('edad_madre', 'N/A')} años
- Edad padre: {patient_data.get('seccion_a', {}).get('edad_padre', 'N/A')} años
- Sexo: {patient_data.get('seccion_a', {}).get('sexo', 'N/A')}
- Fecha de nacimiento: {patient_data.get('seccion_a', {}).get('fecha_nacimiento', 'N/A')}
- Alergias: {patient_data.get('seccion_c', {}).get('alergia', 'No especificadas')}
- Antecedentes: {patient_data.get('seccion_c', {}).get('antecedentes_familiares', 'No especificados')}

Examen físico:
{json.dumps(patient_data.get('examen_fisico', {}), ensure_ascii=False, indent=2)}

Genera entre 2 y 5 recordatorios específicos, relevantes y prácticos para el seguimiento del paciente.
Los recordatorios deben ser:
- Específicos y accionables
- Basados en los datos proporcionados
- Relevantes para el cuidado pediátrico
- Concisos (máximo 15 palabras cada uno)

Responde SOLO con un JSON array de strings, sin texto adicional. Ejemplo:
["Recordar revisar alergias antes de administrar medicamentos", "Seguimiento de temperatura cada 4 horas"]

JSON:"""

            generation_config = GenerationConfig(
                temperature=0.3,
                max_output_tokens=500,
                response_mime_type="application/json"
            )
            
            response = self.model.generate_content(
                prompt,
                generation_config=generation_config
            )
            
            result_text = response.text.strip()
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0].strip()
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0].strip()
            
            reminders = json.loads(result_text)
            if isinstance(reminders, list):
                return reminders[:5]  # Máximo 5 recordatorios
            return []
            
        except Exception as e:
            print(f"[Gemini] Error generando recordatorios inteligentes: {e}")
            return []

    def generate_urgent_alerts(self, patient_data: dict) -> list:
        """
        Genera alertas urgentes automáticas basadas en análisis de los datos del paciente usando IA.
        
        Args:
            patient_data: Diccionario con datos del paciente
        
        Returns:
            Lista de strings con alertas urgentes detectadas
        """
        try:
            examen_fisico = patient_data.get('examen_fisico', {})
            
            prompt = f"""Eres un asistente médico experto en urgencias pediátricas. Analiza los siguientes datos de un paciente y detecta si hay signos de alerta que requieran atención urgente.

Datos del paciente:
- Fecha de nacimiento: {patient_data.get('seccion_a', {}).get('fecha_nacimiento', 'N/A')}
- Alergias: {patient_data.get('seccion_c', {}).get('alergia', 'No especificadas')}

Examen físico:
{json.dumps(examen_fisico, ensure_ascii=False, indent=2)}

Analiza especialmente:
- Signos vitales anormales (temperatura, frecuencia cardíaca, respiratoria)
- Signos de deshidratación o desnutrición
- Signos neurológicos preocupantes
- Signos de infección o sepsis
- Alergias conocidas que puedan afectar el tratamiento
- Cualquier hallazgo que requiera atención inmediata

Si detectas signos de alerta, genera alertas específicas y urgentes.
Si NO hay signos de alerta, responde con un array vacío.

Responde SOLO con un JSON array de strings, sin texto adicional. Cada alerta debe ser:
- Específica y clara
- Indique la urgencia
- Máximo 20 palabras

Ejemplo si hay alertas:
["ALERTA: Temperatura elevada (39.5°C) requiere monitoreo continuo", "ALERTA: Signos de deshidratación detectados - revisar hidratación"]

Ejemplo si NO hay alertas:
[]

JSON:"""

            generation_config = GenerationConfig(
                temperature=0.2,
                max_output_tokens=400,
                response_mime_type="application/json"
            )
            
            response = self.model.generate_content(
                prompt,
                generation_config=generation_config
            )
            
            result_text = response.text.strip()
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0].strip()
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0].strip()
            
            alerts = json.loads(result_text)
            if isinstance(alerts, list):
                return alerts[:3]  # Máximo 3 alertas urgentes
            return []
            
        except Exception as e:
            print(f"[Gemini] Error generando alertas urgentes: {e}")
            return []

    def generate_clinical_summary(self, patient_data: dict, previous_data: dict = None) -> str:
        """
        Genera un resumen clínico inteligente del paciente basado en sus datos usando IA.
        
        Args:
            patient_data: Datos actuales del paciente
            previous_data: Datos anteriores del paciente (opcional, para comparación)
        
        Returns:
            String con resumen clínico
        """
        try:
            comparison_text = ""
            if previous_data:
                comparison_text = f"""
Datos anteriores del paciente (para comparación):
{json.dumps(previous_data, ensure_ascii=False, indent=2)}
"""

            prompt = f"""Eres un médico experto. Genera un resumen clínico conciso y profesional del siguiente paciente pediátrico.

Datos del paciente:
{json.dumps(patient_data, ensure_ascii=False, indent=2)}
{comparison_text}

Genera un resumen clínico que incluya:
1. Datos demográficos principales
2. Hallazgos más relevantes del examen físico
3. Antecedentes importantes
4. Si hay datos anteriores, menciona cambios o evolución significativa
5. Puntos clave para el seguimiento

El resumen debe ser:
- Profesional y claro
- Máximo 200 palabras
- En español médico apropiado
- Enfocado en lo más relevante para el cuidado del paciente

Responde SOLO con el texto del resumen, sin formato adicional ni títulos."""

            generation_config = GenerationConfig(
                temperature=0.4,
                max_output_tokens=800,
            )
            
            response = self.model.generate_content(
                prompt,
                generation_config=generation_config
            )
            
            summary = response.text.strip()
            # Limpiar si viene con markdown
            if summary.startswith("```"):
                summary = summary.split("```")[1].split("```")[0].strip()
                if summary.startswith("markdown") or summary.startswith("text"):
                    lines = summary.split("\n")
                    summary = "\n".join(lines[1:]).strip()
            
            return summary[:1000]  # Limitar a 1000 caracteres
            
        except Exception as e:
            print(f"[Gemini] Error generando resumen clínico: {e}")
            return ""