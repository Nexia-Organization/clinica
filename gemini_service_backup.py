import os
import vertexai
import json
import time
from vertexai.generative_models import GenerativeModel, GenerationConfig
from google.oauth2 import service_account

class GeminiService:
    def __init__(self, credentials_path: str = "gc-key.json", project_id: str = None, location: str = "us-central1"):
        """
        Inicializa el servicio de Gemini usando un archivo de credenciales JSON.
        
        Args:
            credentials_path: Ruta al archivo JSON de credenciales de Google Cloud
            project_id: ID del proyecto (se obtiene del JSON si no se proporciona)
            location: RegiÃ³n de Vertex AI (default: us-central1)
        """
        self.credentials_path = credentials_path
        self.location = location
        
        # Cargar credenciales desde el archivo JSON
        if not os.path.exists(credentials_path):
            raise FileNotFoundError(f"No se encontrÃ³ el archivo de credenciales: {credentials_path}")
        
        # Cargar el JSON para obtener el project_id si no se proporciona
        with open(credentials_path, 'r', encoding='utf-8') as f:
            credentials_data = json.load(f)
        
        # Obtener project_id del JSON o del parÃ¡metro
        self.project_id = project_id or credentials_data.get('project_id') or os.getenv("GOOGLE_CLOUD_PROJECT")
        
        if not self.project_id:
            raise ValueError("No se pudo determinar el project_id. ProporciÃ³nelo como parÃ¡metro o inclÃºyalo en el archivo JSON.")
        
        # Inicializar Vertex AI con las credenciales
        credentials = service_account.Credentials.from_service_account_file(
            credentials_path,
            scopes=['https://www.googleapis.com/auth/cloud-platform']
        )
        
        vertexai.init(project=self.project_id, location=self.location, credentials=credentials)
        self.model = GenerativeModel("gemini-2.0-flash-001")

    def calculate_triage(self, vitals: dict, note: str, edad: str = None, embarazo: bool = False) -> dict:
        """
        Calcula el nivel de triage usando IA (Gemini) segÃºn las reglas institucionales.
        
        Args:
            vitals: Diccionario con signos vitales (ta_sis, ta_dia, sat, fc, fr, temp_c, glasgow)
            note: Nota clÃ­nica con motivo de consulta y observaciones
            edad: Edad del paciente (opcional)
            embarazo: Si el paciente estÃ¡ embarazado (opcional)
        
        Returns:
            Diccionario con level, label, time_max, reasons, color
        """
        
        # Construir prompt con las reglas de triage
        triage_rules = """
TRIAGE 1 - ATENCIÃ“N INMEDIATA (Tiempo mÃ¡ximo: Inmediato)
Criterios:
- Glasgow < 8 (coma profundo)
- SaturaciÃ³n O2 < 85% (hipoxia severa)
- TensiÃ³n arterial sistÃ³lica < 70 mmHg (hipotensiÃ³n severa)
- SÃ­ntomas crÃ­ticos: paro cardiorrespiratorio, apnea, convulsiÃ³n activa, estatus convulsivo, asfixia, hemorragia severa
- Compromiso de vÃ­a aÃ©rea
- Shock

TRIAGE 2 - ALTA PRIORIDAD (Tiempo mÃ¡ximo: 10 minutos)
Criterios:
- Glasgow 8-12 (estupor/coma superficial)
- SaturaciÃ³n O2 85-89% (hipoxia moderada)
- TensiÃ³n arterial sistÃ³lica < 90 mmHg o > 180 mmHg (alteraciÃ³n tensional severa)
- Temperatura â‰¥ 39.0Â°C (fiebre alta)
- SÃ­ntomas urgentes: dolor severo/intenso, disnea, dificultad respiratoria, compromiso de conciencia
- Signos de descompensaciÃ³n hemodinÃ¡mica

TRIAGE 3 - PRIORIDAD MEDIA (Tiempo mÃ¡ximo: 30 minutos)
Criterios:
- Glasgow 13-14 (obnubilaciÃ³n leve)
- SaturaciÃ³n O2 90-93% (saturaciÃ³n baja)
- TensiÃ³n arterial sistÃ³lica 90-99 mmHg o 161-180 mmHg (alteraciÃ³n tensional moderada)
- Temperatura 38.0-38.9Â°C (fiebre moderada)
- SÃ­ntomas moderados: caÃ­da, golpe, herida, vÃ³mito, dolor moderado
- Alteraciones que requieren evaluaciÃ³n pero no son crÃ­ticas

TRIAGE 4 - PRIORIDAD BAJA (Tiempo mÃ¡ximo: 60 minutos)
Criterios:
- Temperatura 37.5-37.9Â°C (febrÃ­cula)
- SÃ­ntomas leves: molestia, incomodidad, consulta no urgente
- Condiciones estables que pueden esperar evaluaciÃ³n

TRIAGE 5 - RUTINA (Tiempo mÃ¡ximo: 120 minutos)
Criterios:
- Estado estable
- Signos vitales normales
- Consulta de rutina o control
- Sin sÃ­ntomas agudos

INSTRUCCIONES:
1. EvalÃºa TODOS los signos vitales proporcionados
2. Considera el motivo de consulta y observaciones en la nota
3. Si hay mÃºltiples criterios, usa el nivel MÃS ALTO (mÃ¡s urgente)
4. Si no hay datos suficientes pero hay sÃ­ntomas crÃ­ticos en la nota, prioriza esos sÃ­ntomas
5. Si todos los signos vitales son normales y no hay sÃ­ntomas, asigna TRIAGE 5
6. Considera la edad del paciente si es relevante (niÃ±os vs adultos tienen diferentes rangos normales)
7. Si hay embarazo, considera que puede haber alteraciones fisiolÃ³gicas normales

RESPONDE ÃšNICAMENTE CON UN JSON en este formato exacto:
{
  "level": 1-5,
  "label": "Nombre del nivel",
  "time_max": "Tiempo mÃ¡ximo de atenciÃ³n",
  "reasons": ["RazÃ³n 1", "RazÃ³n 2", ...],
  "color": "red|orange|yellow|lightblue|green"
}
"""
        
        # Construir datos del paciente para el prompt
        vitals_str = []
        if vitals.get("ta_sis") is not None:
            vitals_str.append(f"TensiÃ³n arterial sistÃ³lica: {vitals.get('ta_sis')} mmHg")
        if vitals.get("ta_dia") is not None:
            vitals_str.append(f"TensiÃ³n arterial diastÃ³lica: {vitals.get('ta_dia')} mmHg")
        if vitals.get("sat") is not None:
            vitals_str.append(f"SaturaciÃ³n O2: {vitals.get('sat')}%")
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

EvalÃºa el triage segÃºn las reglas proporcionadas y responde SOLO con el JSON solicitado.
"""
        
        # Reintentos con backoff exponencial para manejar rate limiting
        max_retries = 3
        base_delay = 1  # segundos
        
        for attempt in range(max_retries):
            try:
                if attempt == 0:
                    print(f"[Gemini] Calculando triage con IA...")
                
                generation_config = GenerationConfig(
                    temperature=0.1,  # Baja temperatura para respuestas mÃ¡s consistentes
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
                    raise ValueError("Nivel de triage invÃ¡lido")
                
                # Asegurar que tenga todos los campos requeridos
                default_labels = {
                    1: "AtenciÃ³n Inmediata",
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
                    "reasons": triage_result.get("reasons", ["EvaluaciÃ³n clÃ­nica"]),
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
                        # Ãšltimo intento fallÃ³, usar fallback
                        print(f"[Gemini] âœ— Rate limit persistente despuÃ©s de {max_retries} intentos. Usando cÃ¡lculo de respaldo.")
                        raise Exception("Rate limit excedido")
                else:
                    # Otro tipo de error
                    print(f"[Gemini] âœ— Error: {error_str[:100]}")
                    raise
        
        # Si llegamos aquÃ­, todos los reintentos fallaron
        raise Exception("Error despuÃ©s de mÃºltiples intentos")
    
    def extract_patient_data_from_image(self, image_path: str) -> dict:
        """
        Extrae datos del paciente desde una imagen de ingreso mÃ©dico usando Gemini Vision.
        
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
Analiza esta imagen de una FICHA DE IDENTIFICACIÃ“N DEL PACIENTE (formulario de ingreso mÃ©dico mexicano) y extrae TODA la informaciÃ³n disponible.

Este formulario tÃ­picamente contiene:
- NOMBRE con campos separados: APELLIDO PATERNO, APELLIDO MATERNO, NOMBRE(S)
- FECHA DE NACIMIENTO (dÃ­a, mes, aÃ±o) - CALCULA LA EDAD si es posible
- SEXO (HOMBRE/MUJER)
- CURP (Clave Ãšnica de Registro de PoblaciÃ³n) - equivalente al DNI
- ESTADO CIVIL, OCUPACIÃ“N
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
- Busca en: "CURP" (Clave Ãšnica de Registro de PoblaciÃ³n)
- TambiÃ©n busca otros nÃºmeros de identificaciÃ³n si CURP no estÃ¡ disponible

CAMPO: edad
- Calcula desde "FECHA DE NACIMIENTO" si estÃ¡ disponible
- Formato: "XX aÃ±os" o "XX aÃ±os, Y meses"
- Si no puedes calcular, busca campos que digan "EDAD" directamente

CAMPO: historia_clinica
- Busca nÃºmeros de expediente, historia clÃ­nica, o identificadores del hospital

CAMPO: sala
- Busca campos como "SALA", "UNIDAD", "ÃREA", "SERVICIO"

CAMPO: cama
- Busca "CAMA", "NÃšMERO DE CAMA"

CAMPO: diagnostico_principal
- Busca "DIAGNÃ“STICO", "DIAGNÃ“STICO PRINCIPAL", "MOTIVO DE INGRESO"

CAMPO: antecedentes
- Busca "ANTECEDENTES", "ALERGIAS", informaciÃ³n mÃ©dica previa
- Incluye tipo de sangre si estÃ¡ disponible: "Tipo de sangre: X" o "Grupo sanguÃ­neo: X"

CAMPO: tipo_clasificacion
- Si es un formulario de identificaciÃ³n nuevo, probablemente es "Ingreso"
- Busca indicadores de "INGRESO", "CONTROL", "PASE", etc.

CAMPO: motivo_consulta
- Busca "MOTIVO DE CONSULTA", "MOTIVO DE INGRESO", "RAZÃ“N DE INGRESO"

CAMPO: ta_sis, ta_dia, sat, fc, fr, temp_c, glasgow
- Busca signos vitales si estÃ¡n en el formulario
- Formato comÃºn: "TA: 120/80", "SAT: 95%", "FC: 80", "FR: 20", "TEMP: 37Â°C"

CAMPO: nivel_conciencia, dolor_escala
- Busca escalas de Glasgow, nivel de conciencia, escala de dolor si estÃ¡n presentes

CAMPO: observaciones
- Incluye cualquier informaciÃ³n adicional relevante:
  - OcupaciÃ³n
  - Estado civil
  - Lengua que habla
  - Domicilio completo
  - TelÃ©fonos de contacto
  - Nombre del familiar responsable
  - Cualquier nota mÃ©dica adicional

CAMPO: embarazo
- true si SEXO es "MUJER" y hay indicios de embarazo en el formulario
- false en otros casos o null si no es aplicable

INSTRUCCIONES ESPECÃFICAS:
1. Si el formulario tiene "APELLIDO PATERNO" y "APELLIDO MATERNO" separados, combÃ­nalos en el campo "apellido"
2. El CURP es el equivalente mexicano al DNI - Ãºsalo para el campo "dni"
3. Calcula la edad desde la fecha de nacimiento si estÃ¡ disponible
4. Si un campo no estÃ¡ presente, devuÃ©lvelo como null
5. Los nÃºmeros deben ser nÃºmeros (no strings), excepto edad que debe ser string como "45 aÃ±os"
6. Los valores booleanos deben ser true/false
7. Si hay mÃºltiples formularios o pacientes en la imagen, extrae solo el primero o el mÃ¡s completo

Responde ÃšNICAMENTE con un JSON vÃ¡lido en este formato exacto:
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
  "ta_sis": nÃºmero o null,
  "ta_dia": nÃºmero o null,
  "sat": nÃºmero o null,
  "fc": nÃºmero o null,
  "fr": nÃºmero o null,
  "temp_c": nÃºmero o null,
  "glasgow": nÃºmero o null,
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
                        print(f"[Gemini Vision] âœ— Rate limit persistente despuÃ©s de {max_retries} intentos.")
                        raise Exception("Rate limit excedido al procesar imagen")
                else:
                    print(f"[Gemini Vision] âœ— Error: {error_str[:200]}")
                    raise
        
        raise Exception("Error despuÃ©s de mÃºltiples intentos al procesar imagen")
    
    def extract_complete_admission_data_from_image(self, image_path: str) -> dict:
        """
        Extrae datos completos del formulario de ingreso pediÃ¡trico desde una imagen usando Gemini Vision.
        Este mÃ©todo estÃ¡ diseÃ±ado para formularios de ingreso completo a salas (sin triage).
        
        Args:
            image_path: Ruta al archivo de imagen
            
        Returns:
            Diccionario con todos los datos extraÃ­dos del formulario pediÃ¡trico completo
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
CRÃTICO: Este formulario contiene ESCRITURA A MANO de mÃ©dicos. 

REGLAS FUNDAMENTALES:
1. **NO INVENTES DATOS**: Si no puedes leer algo con certeza, NO lo completes con informaciÃ³n que no estÃ¡s seguro.
2. **IDENTIFICA CAMPOS ILEGIBLES**: Debes reportar explÃ­citamente quÃ© campos no pudiste interpretar en el campo "campos_ilegibles".
3. **INTERPRETA CON PRECAUCIÃ“N**: Solo interpreta escritura ambigua cuando hay suficiente contexto mÃ©dico que te permita estar razonablemente seguro.
4. **PRESERVA LO LEGIBLE**: Si puedes leer parte de un campo pero no todo, incluye solo lo que lees con certeza.

Analiza esta imagen de un FORMULARIO DE INGRESO PEDIÃTRICO COMPLETO del "Hospital Materno Infantil 'San Roque'" 
(DEPARTAMENTO DE PEDIATRIA - SECTOR TERAPIA INTERMEDIA) y extrae TODA la informaciÃ³n que puedas leer con certeza.

Este formulario contiene mÃºltiples secciones y estÃ¡ ESCRITO A MANO por mÃ©dicos:

**A) APELLIDO Y NOMBRE DEL NIÃ‘O:**
- Apellido y Nombre del niÃ±o
- Sexo, Fecha de nacimiento, Edad
- Apellido y Nombre de la madre, Edad
- Apellido y Nombre del padre, NÂ° de Afiliado
- Mutual (obra social), TelÃ©fono
- Domicilio, MÃ©dico del caso

**B) ENFERMEDAD ACTUAL:**
- Motivo de la consulta
- Fecha de comienzo, evoluciÃ³n, tratamientos instituidos

**C) ANTECEDENTES HEREDITARIOS Y FAMILIARES:**
- TBC, Chagas, Alergia, SÃ­filis, Diabetes
- Enf. Sangre, Enf. NeurolÃ³gicas

**D) ANTECEDENTES PERSONALES:**
- EMBARAZO Y PARTO: EvoluciÃ³n del Embarazo, Drogas, Rx, Ruptura de bolsa
- Periodo Neonatal: Peso Nacimiento, ReanimaciÃ³n, Cianosis, Ictericia, Convulsiones
- NUTRICION
- ANTECEDENTES PSICOMOTORES: Sonrisa Social, SostÃ©n Cabeza, Sentado, Camina, Palabras, Frases, Control EsfÃ­nter
- Escuela: Grado, Problemas
- INMUNIZACIONES

**E) ANTECEDENTES PERSONALES PATOLOGICOS:**
- Cualquier informaciÃ³n patolÃ³gica adicional

**F) DATOS SOCIO-ECONOMICOS:**
- InformaciÃ³n socio-econÃ³mica del paciente y familia

**EXAMEN FISICO:**
- Fecha, Hora, Edad
- Peso, Talla, P.C. (PerÃ­metro CefÃ¡lico), TÂ° Rectal, TÂ° Axilar
- ASPECTO GENERAL
- PSIQUISMO: Normal, Inquietud, Delirio, Obnubilado, Estupor, Coma, Grado
- DECUBITO: Dorsal, Ventral, Indiferente, Activo, Pasivo, Obligado
- PIEL: Color (Cianosis, Palidez, Ictericia), Signo de Pliegue, Humedad, Turgor, Elasticidad
- T.C.S. (Tejido Celular SubcutÃ¡neo): PanÃ­culo Adiposo, Edemas, Ganglios
- CABEZA: CrÃ¡neo (Forma, Fontanela, Facies, TensiÃ³n), Ojos, OÃ­dos, Boca, Nariz, Cuello
- TORAX: InspecciÃ³n, PalpaciÃ³n, PercusiÃ³n, AuscultaciÃ³n, F.R., Disnea, Trajes
- APARATO CARDIOVASCULAR: InspecciÃ³n, PalpaciÃ³n (Pulsos), AuscultaciÃ³n (F.C., Soplo, Arritmia)
- ABDOMEN: InspecciÃ³n, PalpaciÃ³n, PercusiÃ³n, AuscultaciÃ³n
- HIGADO: Borde Superior, Inferior, Superficie, Consistencia
- BAZO: Palpable, TamaÃ±o, Consistencia
- RIÃ‘ON: Palpable, CuÃ¡l, Peloteo
- GENITALES: TestÃ­culos, Bolsas, Prepucio y Glande (masculino) / Labios Mayores y Menores, Flujo (femenino)
- ANO: Normal, AnomalÃ­as, Prolapso, Fisuras
- S.O.M.A. (Sistema Osteomioarticular): Raquis, Extremidades
- SISTEMA NERVIOSO: ParÃ¡lisis, Paresias, Rigidez, Reflejos, Movimientos involuntarios, LocomociÃ³n, Fuerza muscular

**PRESUNCION DIAGNOSTICO:**
- DiagnÃ³stico presuntivo

**PLAN DE ESTUDIO Y TRATAMIENTO INICIAL:**
- Plan de estudios y tratamiento

Mapea TODOS los datos del formulario a este JSON estructurado. 

IMPORTANTE: 
- Si un campo no estÃ¡ presente en el formulario, devuÃ©lvelo como null.
- Si un campo estÃ¡ presente pero es COMPLETAMENTE ILEGIBLE o no puedes interpretarlo con certeza, devuÃ©lvelo como null Y agrÃ©galo a "campos_ilegibles".
- Si puedes leer parte de un campo, incluye solo lo que lees con certeza (no completes lo que no ves).

Responde ÃšNICAMENTE con un JSON vÃ¡lido en este formato exacto:
{
  "campos_ilegibles": [
    "ruta.del.campo.que.no.pudiste.leer",
    "ejemplo: seccion_a.apellido_nombre_nino",
    "ejemplo: examen_fisico.peso"
  ],
  "seccion_a": {
    "apellido_nombre_nino": "string o null",
    "sexo": "string o null",
    "fecha_nacimiento": "string o null",
    "edad": "string o null",
    "apellido_nombre_madre": "string o null",
    "edad_madre": "string o null",
    "apellido_nombre_padre": "string o null",
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
      "Rx": "string o null",
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
    "fecha": "string o null",
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
      "signo_pliegue": "string o null",
      "humedad": "string o null",
      "turgor": "string o null",
      "elasticidad": "string o null",
      "otros": "string o null"
    },
    "tcs": {
      "paniculo_adiposo": "string o null",
      "edemas": "string o null",
      "ganglios": "string o null"
    },
    "cabeza": {
      "craneo_forma": "string o null",
      "fontanela_tamano": "string o null",
      "facies": "string o null",
      "tension": "string o null",
      "ojos": "string o null",
      "oidos": "string o null",
      "boca": "string o null",
      "nariz": "string o null",
      "cuello": "string o null"
    },
    "torax": {
      "inspeccion": "string o null",
      "palpacion": "string o null",
      "percusion": "string o null",
      "auscultacion": "string o null",
      "fr": "string o null",
      "disnea": "string o null",
      "trajes": "string o null"
    },
    "cardiovascular": {
      "inspeccion": "string o null",
      "palpacion_pulsos": "string o null",
      "auscultacion": "string o null",
      "fc": "string o null",
      "soplo": "boolean o null",
      "arritmia": "boolean o null"
    },
    "abdomen": {
      "inspeccion": "string o null",
      "palpacion": "string o null",
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
      "raquis": "string o null",
      "extremidades": "string o null"
    },
    "sistema_nervioso": {
      "paralisis": "boolean o null",
      "paresias": "boolean o null",
      "rigidez": "string o null",
      "reflejos": "string o null",
      "movimientos_involuntarios": "string o null",
      "locomocion": "string o null",
      "fuerza_muscular": "string o null",
      "observaciones": "string o null"
    }
  },
  "presuncion_diagnostico": "string o null",
  "plan_estudio_tratamiento": "string o null"
}

INSTRUCCIONES ESPECÃFICAS PARA ESCRITURA A MANO:

1. **LECTURA DE ESCRITURA A MANO**:
   - Intenta leer TODOS los campos del formulario escritos a mano
   - SÃ© tolerante con diferentes estilos de escritura mÃ©dica
   - Reconoce abreviaciones mÃ©dicas comunes
   - Ten cuidado con nÃºmeros ambiguos (1 vs 7, 0 vs O, 5 vs S) - solo interpreta si el contexto es claro

2. **INTERPRETACIÃ“N CON CERTEZA**:
   - SOLO interpreta escritura ambigua cuando hay suficiente contexto que te permita estar razonablemente seguro
   - Para tÃ©rminos mÃ©dicos: Si puedes leer la mayorÃ­a de las letras y el contexto mÃ©dico es claro, puedes inferir
   - Para nombres propios: Incluye SOLO lo que puedas leer con certeza. Si estÃ¡ parcialmente legible, incluye solo las partes legibles
   - NO completes palabras o nÃºmeros que no puedes ver claramente

3. **MANEJO DE CAMPOS ILEGIBLES**:
   - Si un campo estÃ¡ presente en el formulario pero es COMPLETAMENTE ILEGIBLE, ponlo como null
   - Si un campo es PARCIALMENTE LEGIBLE, incluye solo la parte que lees con certeza
   - Para cada campo que no pudiste interpretar completamente, agrÃ©galo a "campos_ilegibles" con su ruta completa
   - Ejemplo de rutas: "seccion_a.apellido_nombre_nino", "examen_fisico.peso", "seccion_d.embarazo_parto.peso_nacimiento"

4. **CAMPOS ESPECÃFICOS**:
   - Fechas: Intenta leer en cualquier formato (DD/MM/YYYY, DD-MM-YYYY, etc.). Si no puedes leerla completa, incluye solo lo legible
   - NÃºmeros: Pueden tener decimales con coma o punto. Si no puedes leerlo completo, no lo inventes
   - Checkboxes: Solo marca como true si ves una marca clara (X, âœ“, check). Si no hay marca o es ambigua, usa false o null
   - Texto largo: Extrae TODO lo que puedas leer. Si hay partes ilegibles, incluye solo lo legible y marca el campo como parcialmente ilegible

5. **EXTRACCIÃ“N PRECISA**:
   - Extrae TODA la informaciÃ³n que puedas leer con certeza
   - Si hay mÃºltiples pÃ¡ginas o imÃ¡genes, extrae de todas
   - Los valores booleanos: true solo si ves marca clara, false si estÃ¡ vacÃ­o o sin marca, null si no puedes determinarlo
   - Los campos de texto: Preserva exactamente lo que lees (no corrijas ortografÃ­a mÃ©dica)
   - Campos numÃ©ricos: MantÃ©n el formato del formulario (ej: "3.5 kg", "45 cm") solo si puedes leerlo completo

6. **REPORTE DE CAMPOS ILEGIBLES**:
   - El campo "campos_ilegibles" debe ser un array de strings con las rutas de los campos que no pudiste interpretar
   - Usa notaciÃ³n de punto para rutas anidadas: "seccion_a.campo", "examen_fisico.subseccion.campo"
   - Si un campo estÃ¡ parcialmente legible pero no completo, tambiÃ©n agrÃ©galo a "campos_ilegibles" con una nota si es posible
   - Si todos los campos fueron legibles, devuelve un array vacÃ­o: []

RECUERDA: Es MEJOR dejar un campo como null y reportarlo en "campos_ilegibles" que inventar informaciÃ³n que no estÃ¡s seguro.
"""
        
        # Reintentos con backoff exponencial
        max_retries = 3
        base_delay = 1
        
        for attempt in range(max_retries):
            try:
                if attempt == 0:
                    print(f"[Gemini Vision] Analizando imagen para extraer datos de ingreso completo...")
                
                # ConfiguraciÃ³n optimizada para lectura de escritura a mano
                # Temperatura ligeramente mÃ¡s alta para mejor interpretaciÃ³n de escritura ambigua
                generation_config = GenerationConfig(
                    temperature=0.2,  # Aumentada de 0.1 a 0.2 para mejor interpretaciÃ³n de escritura a mano
                    max_output_tokens=4000,
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
                
                print(f"[Gemini Vision] âœ“ Datos de ingreso completo extraÃ­dos exitosamente")
                
                # Parsear respuesta JSON
                result_text = response.text.strip()
                
                # Limpiar si tiene markdown code blocks
                if "```json" in result_text:
                    result_text = result_text.split("```json")[1].split("```")[0].strip()
                elif "```" in result_text:
                    result_text = result_text.split("```")[1].split("```")[0].strip()
                
                admission_data = json.loads(result_text)
                
                return admission_data

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
                        print(f"[Gemini Vision] âœ— Rate limit persistente despuÃ©s de {max_retries} intentos.")
                        raise Exception("Rate limit excedido al procesar imagen")
                else:
                    print(f"[Gemini Vision] âœ— Error: {error_str[:200]}")
                    raise
        
        raise Exception("Error despuÃ©s de mÃºltiples intentos al procesar imagen")
