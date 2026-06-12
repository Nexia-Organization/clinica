import json
import os
import logging
import re
from datetime import datetime
from flask import Flask, jsonify, render_template, request
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
import word_parser
from gemini_service import GeminiService

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max request size (permite múltiples imágenes)
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')

# Manejador de error para archivos demasiado grandes
@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(e):
    return jsonify({
        "error": "El tamaño total de las imágenes es demasiado grande. El límite máximo es 100MB. Por favor, reduce el tamaño de las imágenes o sube menos imágenes a la vez."
    }), 413

# Crear carpeta de uploads si no existe
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

ALLOWED_EXTENSIONS = {'docx', 'doc'}
ALLOWED_IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp'}

# =========================
# Configuración de Logging
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Configurar logging para triage
triage_logger = logging.getLogger('triage_audit')
triage_logger.setLevel(logging.INFO)
triage_handler = logging.FileHandler(
    os.path.join(LOG_DIR, f"triage_audit_{datetime.now().strftime('%Y%m')}.log"),
    encoding='utf-8'
)
triage_handler.setFormatter(logging.Formatter(
    '%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
))
triage_logger.addHandler(triage_handler)

# =========================
# Configuración / Data Load
# =========================

# Inicializar servicio de Gemini para triage usando gc-key.json
try:
    credentials_path = os.path.join(BASE_DIR, "gc-key.json")
    gemini_service = GeminiService(credentials_path=credentials_path)
    print(f"Gemini Service inicializado correctamente con proyecto: {gemini_service.project_id}")
except Exception as e:
    print(f"Advertencia: No se pudo inicializar Gemini Service: {e}")
    print("El sistema usará cálculo de triage basado en reglas como respaldo.")
    gemini_service = None
DATA_PATH = os.environ.get(
    "PARTS_DATA_PATH",
    os.path.join(BASE_DIR, "data", "residentes_db.json"))

_DATA_CACHE = {"mtime": None, "data": None}


def _safe_read_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_data():
    """
    Carga el JSON con cache por mtime.
    Retorna el contenido de residentes_db.json.
    """
    if not os.path.exists(DATA_PATH):
        return {"patients": []}

    try:
        mtime = os.path.getmtime(DATA_PATH)
        if _DATA_CACHE["data"] is None or _DATA_CACHE["mtime"] != mtime:
            _DATA_CACHE["data"] = _safe_read_json(DATA_PATH)
            _DATA_CACHE["mtime"] = mtime

        data = _DATA_CACHE["data"]
        if not isinstance(data, dict):
            return {"patients": []}

        data.setdefault("patients", [])
        return data

    except (json.JSONDecodeError, OSError, IOError) as e:
        return {"patients": [], "error": str(e)}


def save_data(data):
    """
    Guarda los datos en el archivo JSON e invalida el cache.
    """
    try:
        # Crear backup antes de guardar
        backup_path = DATA_PATH + ".backup"
        if os.path.exists(DATA_PATH):
            try:
                import shutil
                shutil.copy2(DATA_PATH, backup_path)
            except:
                pass
        
        # Guardar datos
        with open(DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        # Invalidar cache para que se recargue
        _DATA_CACHE["data"] = None
        _DATA_CACHE["mtime"] = None
        
    except Exception as e:
        raise Exception(f"Error al guardar datos: {str(e)}")


# =========================
# Helpers
# =========================
def _sent_at_to_date(sent_at: str) -> str:
    """
    Convierte '2025-12-21T06:10' -> '2025-12-21'
    Si viene vacío o raro, devuelve ''.
    """
    if not sent_at:
        return ""
    s = str(sent_at)
    # ISO usual
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return ""


def _to_int(x):
    if x is None:
        return None
    try:
        if isinstance(x, (int, float)):
            return int(x)
        s = str(x).strip()
        num = ""
        for ch in s:
            if ch.isdigit():
                num += ch
            elif num:
                break
        return int(num) if num else None
    except:
        return None


def _to_float(x):
    if x is None:
        return None
    try:
        if isinstance(x, (int, float)):
            return float(x)
        s = str(x).strip().replace(",", ".")
        num = ""
        dot_used = False
        for ch in s:
            if ch.isdigit():
                num += ch
            elif ch == "." and not dot_used:
                num += "."
                dot_used = True
            elif num:
                break
        return float(num) if num else None
    except:
        return None


def build_daily_summary_index(daily_list):
    """
    Index para acceso rápido:
      - latest_by_patient[patient_id] = (date, summary_text)
      - by_patient_date[(patient_id, date)] = summary_text
    """
    latest_by_patient = {}
    by_patient_date = {}

    for row in daily_list or []:
        pid = row.get("patient_id")
        date = row.get("date")
        text = row.get("family_friendly_summary")
        if not pid or not date or not text:
            continue

        by_patient_date[(pid, date)] = text

        prev = latest_by_patient.get(pid)
        # comparo por date ISO (YYYY-MM-DD) => string compare sirve
        if prev is None or str(date) > str(prev[0]):
            latest_by_patient[pid] = (date, text)

    return latest_by_patient, by_patient_date


# =========================
# Lógica central (riesgo y triage)
# =========================

def calculate_triage_level(p_data, patient_info=None, use_ai=False):
    """
    Calcula el nivel de triage según protocolo institucional (1-5).
    
    Args:
        p_data: Datos del paciente/shift con vitals y note
        patient_info: Información adicional del paciente (edad, embarazo)
        use_ai: Si es True, intenta usar Gemini. Si es False, usa solo reglas hardcodeadas.
    
    Por defecto usa cálculo basado en reglas para evitar rate limiting.
    Solo usa IA cuando use_ai=True (ej: al registrar un nuevo paciente).
    """
    v = (p_data.get("vitals", {}) or {}) if isinstance(p_data, dict) else {}
    note = (p_data.get("note", "") or "") if isinstance(p_data, dict) else ""
    
    # Solo usar Gemini si se solicita explícitamente (ej: registro de paciente)
    if use_ai and gemini_service:
        try:
            edad = None
            embarazo = False
            if patient_info:
                edad = patient_info.get("edad")
                embarazo = patient_info.get("embarazo", False)
            
            triage_result = gemini_service.calculate_triage(
                vitals=v,
                note=note,
                edad=edad,
                embarazo=embarazo
            )
            return triage_result
        except Exception as e:
            # Solo mostrar error si no es rate limiting (para no saturar logs)
            error_str = str(e)
            if "429" not in error_str and "Resource exhausted" not in error_str:
                print(f"[Fallback] Error usando Gemini, usando cálculo basado en reglas: {e}")
            # Continuar con fallback
    
    # Cálculo basado en reglas (usado por defecto para visualización)
    return _calculate_triage_fallback(v, note)


def _calculate_triage_fallback(v, note):
    """
    Cálculo de triage de respaldo usando reglas hardcodeadas.
    Se usa si Gemini no está disponible.
    """
    note_lower = note.lower() if note else ""
    
    sat = _to_int(v.get("sat"))
    ta_sis = _to_int(v.get("ta_sis"))
    ta_dia = _to_int(v.get("ta_dia"))
    temp_c = _to_float(v.get("temp_c"))
    fc = _to_int(v.get("fc"))
    fr = _to_int(v.get("fr"))
    glasgow = _to_int(v.get("glasgow"))
    
    # TRIAGE 1 - ATENCIÓN INMEDIATA
    if glasgow is not None and glasgow < 8:
        return {
            "level": 1,
            "label": "Atención Inmediata",
            "time_max": "Inmediato",
            "reasons": [f"Glasgow crítico ({glasgow})"],
            "color": "red"
        }
    
    if sat is not None and sat < 85:
        return {
            "level": 1,
            "label": "Atención Inmediata",
            "time_max": "Inmediato",
            "reasons": ["Hipoxia Severa (Sat < 85%)"],
            "color": "red"
        }
    
    if ta_sis is not None and ta_sis < 70:
        return {
            "level": 1,
            "label": "Atención Inmediata",
            "time_max": "Inmediato",
            "reasons": ["Hipotensión Severa"],
            "color": "red"
        }
    
    critical_keywords = ["paro", "apnea", "convulsion", "convulsión", "estatus", "asfixia", "hemorragia severa"]
    if any(kw in note_lower for kw in critical_keywords):
        return {
            "level": 1,
            "label": "Atención Inmediata",
            "time_max": "Inmediato",
            "reasons": ["Síntoma crítico detectado"],
            "color": "red"
        }
    
    # TRIAGE 2 - MANEJO DENTRO DE 10 MINUTOS
    if glasgow is not None and 8 <= glasgow < 13:
        return {
            "level": 2,
            "label": "Alta Prioridad",
            "time_max": "10 minutos",
            "reasons": [f"Glasgow alterado ({glasgow})"],
            "color": "orange"
        }
    
    if sat is not None and 85 <= sat < 90:
        return {
            "level": 2,
            "label": "Alta Prioridad",
            "time_max": "10 minutos",
            "reasons": ["Hipoxia Moderada"],
            "color": "orange"
        }
    
    if ta_sis is not None and (ta_sis < 90 or ta_sis > 180):
        return {
            "level": 2,
            "label": "Alta Prioridad",
            "time_max": "10 minutos",
            "reasons": ["Alteración Tensional Severa"],
            "color": "orange"
        }
    
    if temp_c is not None and temp_c >= 39.0:
        return {
            "level": 2,
            "label": "Alta Prioridad",
            "time_max": "10 minutos",
            "reasons": ["Fiebre Alta"],
            "color": "orange"
        }
    
    urgent_keywords = ["dolor severo", "dolor intenso", "disnea", "dificultad respiratoria", "compromiso conciencia"]
    if any(kw in note_lower for kw in urgent_keywords):
        return {
            "level": 2,
            "label": "Alta Prioridad",
            "time_max": "10 minutos",
            "reasons": ["Síntoma urgente"],
            "color": "orange"
        }
    
    # TRIAGE 3 - ATENCIÓN DENTRO DE 30 MINUTOS
    if glasgow is not None and 13 <= glasgow < 15:
        return {
            "level": 3,
            "label": "Prioridad Media",
            "time_max": "30 minutos",
            "reasons": [f"Glasgow moderado ({glasgow})"],
            "color": "yellow"
        }
    
    if sat is not None and 90 <= sat < 94:
        return {
            "level": 3,
            "label": "Prioridad Media",
            "time_max": "30 minutos",
            "reasons": ["Saturación Baja"],
            "color": "yellow"
        }
    
    if ta_sis is not None and (90 <= ta_sis < 100 or 160 < ta_sis <= 180):
        return {
            "level": 3,
            "label": "Prioridad Media",
            "time_max": "30 minutos",
            "reasons": ["Alteración Tensional"],
            "color": "yellow"
        }
    
    if temp_c is not None and 38.0 <= temp_c < 39.0:
        return {
            "level": 3,
            "label": "Prioridad Media",
            "time_max": "30 minutos",
            "reasons": ["Fiebre Moderada"],
            "color": "yellow"
        }
    
    moderate_keywords = ["caida", "caída", "golpe", "herida", "vomito", "vómito", "dolor"]
    if any(kw in note_lower for kw in moderate_keywords):
        return {
            "level": 3,
            "label": "Prioridad Media",
            "time_max": "30 minutos",
            "reasons": ["Síntoma moderado"],
            "color": "yellow"
        }
    
    # TRIAGE 4 - ATENCIÓN DENTRO DE 60 MINUTOS
    if temp_c is not None and 37.5 <= temp_c < 38.0:
        return {
            "level": 4,
            "label": "Prioridad Baja",
            "time_max": "60 minutos",
            "reasons": ["Febrícula"],
            "color": "lightblue"
        }
    
    if any(kw in note_lower for kw in ["molestia", "incomodidad", "consulta"]):
        return {
            "level": 4,
            "label": "Prioridad Baja",
            "time_max": "60 minutos",
            "reasons": ["Consulta no urgente"],
            "color": "lightblue"
        }
    
    # TRIAGE 5 - EVALUACIÓN DENTRO DE 120 MINUTOS (default)
    return {
        "level": 5,
        "label": "Rutina",
        "time_max": "120 minutos",
        "reasons": ["Estado estable"],
        "color": "green"
    }


def calculate_risk_profile(p_data):
    """
    Calcula perfil de riesgo (compatibilidad con código existente).
    Ahora también incluye información de triage.
    """
    score = 0
    reasons = []
    v = (p_data.get("vitals", {}) or {}) if isinstance(p_data, dict) else {}

    sat = _to_int(v.get("sat"))
    ta_sis = _to_int(v.get("ta_sis"))
    temp_c = _to_float(v.get("temp_c"))

    # Reglas
    if sat is not None:
        if sat < 90:
            score += 50
            reasons.append("Hipoxia Crítica")
        elif 90 <= sat < 94:
            score += 20
            reasons.append("Saturación Baja")

    if ta_sis is not None:
        if ta_sis > 170:
            score += 35
            reasons.append("HTA Severa")
        elif ta_sis < 90:
            score += 40
            reasons.append("Hipotensión")

    if temp_c is not None:
        if temp_c >= 38.5:
            score += 30
            reasons.append("Fiebre Alta")
        elif 37.5 <= temp_c < 38.5:
            score += 10
            reasons.append("Febrícula")

    note = (p_data.get("note", "") or "").lower() if isinstance(p_data, dict) else ""
    danger_keywords = {
        "caida": 45,
        "caída": 45,
        "golpe": 45,
        "sangre": 40,
        "disnea": 50,
        "asfixia": 50,
        "desorientado": 25,
        "desorientada": 25,
    }
    for word, points in danger_keywords.items():
        if word in note:
            score += points
            reasons.append(f"Alerta: {word.capitalize()}")

    level = "Alto" if score >= 50 else "Medio" if score >= 20 else "Normal"
    
    # Incluir triage en el resultado
    triage = calculate_triage_level(p_data)
    
    return {
        "score": score,
        "level": level,
        "reasons": list(set(reasons)),
        "triage": triage
    }


# =========================
# Rutas
# =========================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/patients/list", methods=["GET"])
def list_patients():
    data = load_data()
    patients = data.get("patients", []) or []

    final_list = []
    for p in patients:
        pid = p.get("patient_id")
        pname = p.get("patient")
        if not pid:
            continue
        
        # Obtener el último shift para obtener unidad y calcular riesgo
        shifts = p.get("shifts", []) or []
        latest_shift = None
        unit = ""
        
        if shifts:
            # Ordenar por sent_at, o datetime como fallback, o fecha_ingreso del paciente
            latest_shift = sorted(shifts, key=lambda x: (
                x.get("sent_at") or 
                x.get("datetime") or 
                p.get("fecha_ingreso", "") or 
                ""
            ), reverse=True)[0]
            unit = latest_shift.get("unit", "")
        
        # Priorizar triage_inicial si existe (triage modificado manualmente durante registro)
        # Solo calcular desde shift si no hay triage_inicial
        triage_inicial = p.get("triage_inicial", {})
        if triage_inicial and triage_inicial.get("level"):
            # Usar el triage inicial guardado (puede ser modificado manualmente)
            triage = triage_inicial
            # Calcular riesgo desde el último shift si existe, sino usar risk_inicial
            if latest_shift:
                risk_profile = calculate_risk_profile(latest_shift)
            else:
                risk_profile = p.get("risk_inicial", {})
        else:
            # Si no hay triage_inicial, calcular desde el último shift
            if latest_shift:
                risk_profile = calculate_risk_profile(latest_shift)
                triage = risk_profile.get("triage", {})
            else:
                # Si no hay shifts ni triage_inicial, usar valores por defecto
                triage = {}
                risk_profile = {}
        
        # Obtener fecha de ingreso para ordenamiento
        fecha_ingreso = p.get("fecha_ingreso", "")
        
        # Verificar si tiene anotaciones urgentes o recordatorios
        annotations = p.get("annotations", {})
        has_urgent_notes = bool(annotations.get("urgent_notes", "").strip())
        has_reminders = bool(annotations.get("reminders", "").strip())
        has_important_notes = has_urgent_notes or has_reminders
        
        final_list.append({
            "id": pid,
            "name": pname or pid,
            "triage_level": triage.get("level", 5),
            "triage_label": triage.get("label", "Rutina"),
            "triage_color": triage.get("color", "green"),
            "unit": unit,
            "risk_score": risk_profile.get("score", 0) if isinstance(risk_profile, dict) else 0,
            "fecha_ingreso": fecha_ingreso,
            "has_urgent_notes": has_urgent_notes,
            "has_reminders": has_reminders,
            "has_important_notes": has_important_notes
        })

    # Ordenar por: 1) triage_level (menor = más urgente), 2) risk_score (mayor = más riesgo), 3) fecha_ingreso (más antiguo primero)
    final_list.sort(key=lambda x: (
        x["triage_level"],  # Prioridad: menor = más urgente
        -x["risk_score"],   # Riesgo: mayor = más riesgo (negativo para orden descendente)
        x["fecha_ingreso"] or "9999-12-31"  # Orden de llegada: más antiguo primero
    ))
    return jsonify(final_list)


@app.route("/api/reports/<patient_id>", methods=["GET"])
def get_reports(patient_id):
    data = load_data()
    patients = data.get("patients", []) or []

    patient_obj = None
    for p in patients:
        if p.get("patient_id") == patient_id:
            patient_obj = p
            break

    if not patient_obj:
        return jsonify({"reports": [], "pharmacy": [], "summary": None}), 404

    enfermeria_shifts = patient_obj.get("shifts", []) or []
    pharmacy_shifts = patient_obj.get("shiftsPharmacy", []) or []

    # Preparar reportes de enfermería con triage
    reports = []
    for s in enfermeria_shifts:
        risk_profile = calculate_risk_profile(s)
        reports.append({
            "shift_label": s.get("shift_label", "Guardia"),
            "sent_at": s.get("sent_at"),
            "posted_by": s.get("posted_by"),
            "unit": s.get("unit", ""),
            "data": s,
            "risk": risk_profile,
            "triage": risk_profile.get("triage", {})
        })
    
    # Ordenar reportes por fecha descendente
    reports.sort(key=lambda x: (x["sent_at"] or ""), reverse=True)

    # El resumen para la familia lo sacamos del reporte más reciente que tenga family_friendly_summary
    # o de la última nota si no hay.
    summary_text = None
    summary_meta = None

    for r in reports:
        ff = r["data"].get("family_friendly_summary")
        if ff and ff.strip():
            summary_text = ff
            summary_meta = {
                "sent_at": r["sent_at"],
                "posted_by": r["posted_by"],
                "shift_label": r["shift_label"]
            }
            break

    # Información adicional del paciente
    patient_info = {
        "patient_id": patient_obj.get("patient_id"),
        "patient": patient_obj.get("patient"),
        "nickname": patient_obj.get("nickname", ""),
        "unit": reports[0].get("unit", "") if reports else "",
        "latest_triage": reports[0].get("triage", {}) if reports else {}
    }

    return jsonify({
        "patient": patient_info,
        "reports": reports,
        "pharmacy": pharmacy_shifts,
        "summary": summary_text,
        "summary_meta": summary_meta
    })


@app.route("/api/patients/<patient_id>/annotations", methods=["GET"])
def get_patient_annotations(patient_id):
    """
    Obtiene las anotaciones (urgentes, recordatorios, historia clínica) de un paciente.
    """
    try:
        data = load_data()
        patients = data.get("patients", []) or []
        
        patient_obj = None
        for p in patients:
            if p.get("patient_id") == patient_id:
                patient_obj = p
                break
        
        if not patient_obj:
            return jsonify({"error": "Paciente no encontrado"}), 404
        
        annotations = patient_obj.get("annotations", {})
        
        return jsonify({
            "patient_id": patient_id,
            "annotations": {
                "urgent_notes": annotations.get("urgent_notes", ""),
                "reminders": annotations.get("reminders", ""),
                "clinical_history": annotations.get("clinical_history", "")
            }
        })
        
    except Exception as e:
        error_msg = str(e)
        print(f"[Get Annotations] ✗ Error: {error_msg}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Error al obtener anotaciones: {error_msg}"}), 500


@app.route("/api/patients/<patient_id>/ai-suggestions", methods=["POST"])
def get_ai_suggestions(patient_id):
    """
    Genera sugerencias de IA (recordatorios, alertas urgentes, resumen clínico) para un paciente.
    """
    try:
        if not gemini_service:
            return jsonify({"error": "Servicio de IA no disponible"}), 500
        
        data = load_data()
        patients = data.get("patients", []) or []
        
        patient_obj = None
        for p in patients:
            if p.get("patient_id") == patient_id:
                patient_obj = p
                break
        
        if not patient_obj:
            return jsonify({"error": "Paciente no encontrado"}), 404
        
        # Obtener datos completos del paciente
        complete_data = patient_obj.get("complete_admission_data", {})
        if not complete_data:
            return jsonify({
                "reminders": [],
                "urgent_alerts": [],
                "clinical_summary": "No hay datos completos del paciente para generar sugerencias."
            })
        
        # Generar sugerencias con IA
        reminders = gemini_service.generate_smart_reminders(complete_data)
        urgent_alerts = gemini_service.generate_urgent_alerts(complete_data)
        clinical_summary = gemini_service.generate_clinical_summary(complete_data)
        
        return jsonify({
            "reminders": reminders,
            "urgent_alerts": urgent_alerts,
            "clinical_summary": clinical_summary
        })
        
    except Exception as e:
        error_msg = str(e)
        print(f"[AI Suggestions] ✗ Error: {error_msg}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Error al generar sugerencias de IA: {error_msg}"}), 500


@app.route("/api/patients/<patient_id>/annotations", methods=["POST"])
def update_patient_annotations(patient_id):
    """
    Actualiza las anotaciones (urgentes, recordatorios, historia clínica) de un paciente.
    """
    try:
        request_data = request.get_json()
        if not request_data:
            return jsonify({"error": "No se proporcionaron datos"}), 400
        
        data = load_data()
        patients = data.get("patients", []) or []
        
        patient_obj = None
        patient_index = -1
        for i, p in enumerate(patients):
            if p.get("patient_id") == patient_id:
                patient_obj = p
                patient_index = i
                break
        
        if not patient_obj:
            return jsonify({"error": "Paciente no encontrado"}), 404
        
        # Inicializar annotations si no existe
        if "annotations" not in patient_obj:
            patient_obj["annotations"] = {}
        
        # Actualizar solo los campos proporcionados
        if "urgent_notes" in request_data:
            patient_obj["annotations"]["urgent_notes"] = request_data.get("urgent_notes", "")
        
        if "reminders" in request_data:
            patient_obj["annotations"]["reminders"] = request_data.get("reminders", "")
        
        if "clinical_history" in request_data:
            patient_obj["annotations"]["clinical_history"] = request_data.get("clinical_history", "")
        
        # Actualizar en la lista
        patients[patient_index] = patient_obj
        data["patients"] = patients
        
        # Guardar datos
        save_data(data)
        
        print(f"[Update Annotations] ✓ Anotaciones actualizadas para paciente: {patient_id}")
        
        return jsonify({
            "success": True,
            "message": "Anotaciones actualizadas exitosamente",
            "annotations": patient_obj["annotations"]
        })
        
    except Exception as e:
        error_msg = str(e)
        print(f"[Update Annotations] ✗ Error: {error_msg}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Error al actualizar anotaciones: {error_msg}"}), 500


@app.route("/api/risk_radar", methods=["GET"])
def get_risk_radar():
    data = load_data()
    patients = data.get("patients", []) or []

    radar = {}

    for p in patients:
        pid = p.get("patient_id")
        pname = p.get("patient") or ""
        if not pid:
            continue
        
        # Priorizar triage_inicial si existe (triage modificado manualmente durante registro)
        triage_inicial = p.get("triage_inicial", {})
        
        # Evaluar riesgo usando el último reporte de enfermería
        shifts = p.get("shifts", []) or []
        if not shifts and not triage_inicial:
            continue
        
        # Calcular riesgo desde el último shift si existe
        if shifts:
            latest_shift = sorted(shifts, key=lambda x: (x.get("sent_at") or ""), reverse=True)[0]
            current_risk = calculate_risk_profile(latest_shift)
        else:
            current_risk = p.get("risk_inicial", {})
        
        # Usar triage_inicial si existe, sino usar el calculado desde el shift
        if triage_inicial and triage_inicial.get("level"):
            triage = triage_inicial
        else:
            triage = current_risk.get("triage", {})

        if current_risk["score"] > 0 or triage.get("level", 5) <= 3:
            radar[pid] = {
                "id": pid,
                "name": pname,
                "score": current_risk["score"],
                "reasons": list(current_risk["reasons"]),
                "triage_level": triage.get("level", 5),
                "triage_label": triage.get("label", "Rutina"),
                "unit": latest_shift.get("unit", ""),
            }

    top_critical = sorted(radar.values(), key=lambda x: (x["triage_level"], -x["score"]))[:15]
    return jsonify(top_critical)


@app.route("/api/patients/by_unit", methods=["GET"])
def get_patients_by_unit():
    """Retorna pacientes agrupados por unidad/sala"""
    data = load_data()
    patients = data.get("patients", []) or []
    
    by_unit = {}
    
    for p in patients:
        pid = p.get("patient_id")
        pname = p.get("patient") or ""
        shifts = p.get("shifts", []) or []
        
        if not shifts:
            unit = "Sin asignar"
        else:
            latest_shift = sorted(shifts, key=lambda x: (x.get("sent_at") or ""), reverse=True)[0]
            unit = latest_shift.get("unit", "Sin asignar")
        
        if unit not in by_unit:
            by_unit[unit] = []
        
        # Priorizar triage_inicial si existe (triage modificado manualmente durante registro)
        triage_inicial = p.get("triage_inicial", {})
        if triage_inicial and triage_inicial.get("level"):
            # Usar el triage inicial guardado (puede ser modificado manualmente)
            triage = triage_inicial
            # Calcular riesgo desde el último shift si existe
            latest_shift = sorted(shifts, key=lambda x: (x.get("sent_at") or ""), reverse=True)[0] if shifts else {}
            risk_profile = calculate_risk_profile(latest_shift) if latest_shift else p.get("risk_inicial", {})
        else:
            # Si no hay triage_inicial, calcular desde el último reporte
            latest_shift = sorted(shifts, key=lambda x: (x.get("sent_at") or ""), reverse=True)[0] if shifts else {}
            risk_profile = calculate_risk_profile(latest_shift) if latest_shift else {}
            triage = risk_profile.get("triage", {})
        
        # Obtener fecha de ingreso para ordenamiento
        fecha_ingreso = p.get("fecha_ingreso", "")
        
        by_unit[unit].append({
            "id": pid,
            "name": pname,
            "nickname": p.get("nickname", ""),
            "triage_level": triage.get("level", 5),
            "triage_label": triage.get("label", "Rutina"),
            "triage_color": triage.get("color", "green"),
            "risk_score": risk_profile.get("score", 0),
            "last_update": latest_shift.get("sent_at", "") if latest_shift else "",
            "fecha_ingreso": fecha_ingreso
        })
    
    # Ordenar pacientes dentro de cada unidad por: 1) triage, 2) riesgo, 3) fecha de ingreso
    for unit in by_unit:
        by_unit[unit].sort(key=lambda x: (
            x["triage_level"],  # Prioridad: menor = más urgente
            -x.get("risk_score", 0),  # Riesgo: mayor = más riesgo
            x.get("last_update", "") or x.get("fecha_ingreso", "") or "9999-12-31"  # Orden de llegada
        ))
    
    return jsonify(by_unit)


@app.route("/api/health", methods=["GET"])
def health():
    d = load_data()
    ok = "error" not in d
    return jsonify(
        {
            "ok": ok,
            "data_path": DATA_PATH,
            "shifts": len(d.get("shifts", []) or []),
            "daily_summaries": len(d.get("daily_patient_summaries_active", []) or []),
            "error": d.get("error"),
        }
    )


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def allowed_image_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


@app.route("/api/calculate_triage", methods=["POST"])
def calculate_triage_endpoint():
    """Endpoint auxiliar para calcular triage desde el frontend usando IA (previsualización antes de registrar)"""
    try:
        data = request.get_json()
        vitals = data.get("vitals", {})
        note = data.get("note", "")
        edad = data.get("edad")
        embarazo = data.get("embarazo", False)
        
        shift_data = {
            "vitals": vitals,
            "note": note
        }
        
        patient_info = {
            "edad": edad,
            "embarazo": embarazo
        }
        
        # Usar IA para previsualización (use_ai=True)
        triage_result = calculate_triage_level(shift_data, patient_info, use_ai=True)
        risk_profile = calculate_risk_profile(shift_data)
        
        return jsonify({
            "triage": triage_result,
            "risk": risk_profile
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/patients/register", methods=["POST"])
def register_patient():
    """
    Endpoint para registrar un nuevo paciente con todos los campos requeridos
    y realizar triage automático.
    """
    try:
        data = request.get_json()
        
        # Validar campos requeridos
        required_fields = ["nombre", "apellido", "edad"]
        for field in required_fields:
            if not data.get(field):
                return jsonify({"error": f"Campo requerido faltante: {field}"}), 400
        
        # Generar patient_id único
        import hashlib
        nombre = (data.get('nombre', '') or '').strip().lower()
        apellido = (data.get('apellido', '') or '').strip().lower()
        dni = (data.get('dni', '') or '').strip()
        
        name_for_id = f"{nombre}{apellido}{dni}".replace(" ", "")
        patient_id = f"pat_{hashlib.md5(name_for_id.encode()).hexdigest()[:8]}"
        
        # Verificar si el paciente ya existe por nombre + DNI
        existing_data = load_data()
        existing_patients = existing_data.get("patients", [])
        
        def normalize_existing_name(p):
            """Normaliza el nombre de un paciente existente para comparación"""
            existing_nombre = (p.get("nickname", "") or "").strip().lower()
            existing_apellido = ""
            # Extraer apellido del campo "patient" que puede tener formato "Apellido, Nombre" o "Nombre Apellido"
            existing_patient = (p.get("patient", "") or "").strip()
            if "," in existing_patient:
                parts = existing_patient.split(",", 1)
                existing_apellido = parts[0].strip().lower()
                if not existing_nombre:
                    existing_nombre = parts[1].strip().lower()
            else:
                # Si no tiene coma, puede ser "Nombre Apellido" o solo nombre
                # Intentar usar campos separados si existen
                if p.get("nombre") and p.get("apellido"):
                    existing_nombre = (p.get("nombre", "") or "").strip().lower()
                    existing_apellido = (p.get("apellido", "") or "").strip().lower()
                elif existing_patient:
                    # Intentar separar por espacios
                    parts = existing_patient.split()
                    if len(parts) >= 2:
                        existing_nombre = parts[0].lower()
                        existing_apellido = " ".join(parts[1:]).lower()
                    else:
                        existing_nombre = existing_patient.lower()
            return existing_nombre, existing_apellido
        
        for p in existing_patients:
            # Verificar por patient_id
            if p.get("patient_id") == patient_id:
                return jsonify({"error": "El paciente ya existe en el sistema"}), 400
            
            # Normalizar nombre del paciente existente
            existing_nombre, existing_apellido = normalize_existing_name(p)
            existing_dni = (p.get("dni", "") or "").strip()
            
            # SOLO comparar por nombre + apellido + DNI si ambos tienen DNI y coinciden
            if dni and existing_dni:
                if dni == existing_dni:
                    if nombre == existing_nombre and apellido == existing_apellido:
                        return jsonify({"error": "El paciente ya existe en el sistema (mismo nombre y DNI)"}), 400
            # Si el nuevo tiene DNI pero el existente no, o viceversa, no son el mismo paciente
            # (no comparar solo por nombre si hay diferencia en DNI)
        
        # Preparar datos del paciente
        from datetime import datetime
        fecha_ingreso = data.get("fecha_ingreso") or datetime.now().isoformat(timespec="minutes")
        
        # Preparar signos vitales para triage
        vitals = {
            "ta_sis": _to_int(data.get("ta_sis")),
            "ta_dia": _to_int(data.get("ta_dia")),
            "sat": _to_int(data.get("sat")),
            "fc": _to_int(data.get("fc")),
            "fr": _to_int(data.get("fr")),
            "temp_c": _to_float(data.get("temp_c")),
            "glasgow": _to_int(data.get("glasgow"))
        }
        
        # Preparar nota con motivo de consulta y observaciones
        motivo_consulta = data.get("motivo_consulta", "")
        observaciones = data.get("observaciones", "")
        nivel_conciencia = data.get("nivel_conciencia", "")
        dolor_escala = data.get("dolor_escala", "")
        
        note_parts = []
        if motivo_consulta:
            note_parts.append(f"Motivo de consulta: {motivo_consulta}")
        if nivel_conciencia:
            note_parts.append(f"Nivel de conciencia: {nivel_conciencia}")
        if dolor_escala:
            note_parts.append(f"Dolor (escala): {dolor_escala}")
        if observaciones:
            note_parts.append(f"Observaciones: {observaciones}")
        
        note = ". ".join(note_parts)
        
        # Crear shift inicial con los datos del registro
        shift_data = {
            "vitals": vitals,
            "note": note
        }
        
        # Calcular triage sugerido por el sistema (usando IA - solo aquí se usa Gemini)
        patient_info_for_triage = {
            "edad": data.get("edad"),
            "embarazo": data.get("embarazo", False)
        }
        triage_sugerido = calculate_triage_level(shift_data, patient_info_for_triage, use_ai=True)
        risk_profile = calculate_risk_profile(shift_data)
        
        # El profesional puede modificar el triage
        triage_final_nivel = data.get("triage_final_level")
        profesional_registro = data.get("profesional_registro", "Sistema")
        justificacion = data.get("justificacion_triage", "")
        nivel_sugerido = triage_sugerido.get("level", 5)
        
        if triage_final_nivel:
            nivel_final = int(triage_final_nivel)
            
            # Verificar si realmente se modificó el nivel
            fue_modificado = (nivel_final != nivel_sugerido)
            
            # Mapear colores según el nivel de triage
            triage_color_map = {
                1: "red",
                2: "orange",
                3: "yellow",
                4: "lightblue",
                5: "green"
            }
            color_final = triage_color_map.get(nivel_final, "green")
            
            # Si el profesional especificó un nivel diferente, usar ese
            triage_result = {
                "level": nivel_final,
                "label": data.get("triage_final_label", triage_sugerido.get("label", "Rutina")),
                "time_max": data.get("triage_final_time", triage_sugerido.get("time_max", "120 minutos")),
                "reasons": data.get("triage_final_reasons", triage_sugerido.get("reasons", [])),
                "color": color_final,
                "sugerido_por_sistema": triage_sugerido,
                "modificado_por_profesional": fue_modificado,
                "justificacion_modificacion": justificacion if fue_modificado else ""
            }
            
            # LOG: Triage modificado o confirmado por profesional
            if fue_modificado:
                # LOG: Triage modificado por profesional
                triage_logger.info(
                    f"TRIAGE MODIFICADO | "
                    f"Paciente: {patient_id} | "
                    f"Profesional: {profesional_registro} | "
                    f"Nivel Sugerido: {nivel_sugerido} ({triage_sugerido.get('label', 'N/A')}) | "
                    f"Nivel Final: {nivel_final} ({triage_result.get('label', 'N/A')}) | "
                    f"Justificación: {justificacion if justificacion else 'No proporcionada'} | "
                    f"Signos Vitales: TA={vitals.get('ta_sis', 'N/A')}/{vitals.get('ta_dia', 'N/A')}, "
                    f"SAT={vitals.get('sat', 'N/A')}, FC={vitals.get('fc', 'N/A')}, "
                    f"Temp={vitals.get('temp_c', 'N/A')}, Glasgow={vitals.get('glasgow', 'N/A')} | "
                    f"Motivo: {motivo_consulta[:100] if motivo_consulta else 'N/A'}"
                )
            else:
                # LOG: Triage confirmado (no modificado)
                triage_logger.info(
                    f"TRIAGE CONFIRMADO | "
                    f"Paciente: {patient_id} | "
                    f"Profesional: {profesional_registro} | "
                    f"Nivel: {nivel_final} ({triage_result.get('label', 'N/A')}) | "
                    f"Tiempo Máximo: {triage_result.get('time_max', 'N/A')} | "
                    f"Razones: {', '.join(triage_result.get('reasons', []))} | "
                    f"Signos Vitales: TA={vitals.get('ta_sis', 'N/A')}/{vitals.get('ta_dia', 'N/A')}, "
                    f"SAT={vitals.get('sat', 'N/A')}, FC={vitals.get('fc', 'N/A')}, "
                    f"Temp={vitals.get('temp_c', 'N/A')}, Glasgow={vitals.get('glasgow', 'N/A')} | "
                    f"Motivo: {motivo_consulta[:100] if motivo_consulta else 'N/A'}"
                )
        else:
            # Usar el sugerido por el sistema (sin selección explícita del profesional)
            triage_result = triage_sugerido
            triage_result["sugerido_por_sistema"] = triage_sugerido
            triage_result["modificado_por_profesional"] = False
            
            # LOG: Triage confirmado por profesional (sin modificación)
            triage_logger.info(
                f"TRIAGE CONFIRMADO | "
                f"Paciente: {patient_id} | "
                f"Profesional: {profesional_registro} | "
                f"Nivel: {nivel_sugerido} ({triage_sugerido.get('label', 'N/A')}) | "
                f"Tiempo Máximo: {triage_sugerido.get('time_max', 'N/A')} | "
                f"Razones: {', '.join(triage_sugerido.get('reasons', []))} | "
                f"Signos Vitales: TA={vitals.get('ta_sis', 'N/A')}/{vitals.get('ta_dia', 'N/A')}, "
                f"SAT={vitals.get('sat', 'N/A')}, FC={vitals.get('fc', 'N/A')}, "
                f"Temp={vitals.get('temp_c', 'N/A')}, Glasgow={vitals.get('glasgow', 'N/A')} | "
                f"Motivo: {motivo_consulta[:100] if motivo_consulta else 'N/A'}"
            )
        
        # Crear shift de enfermería inicial
        initial_shift = {
            "shift_id": f"register_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{patient_id}",
            "posted_by": data.get("profesional_registro", "Sistema"),
            "posted_by_id": data.get("profesional_id", "system_001"),
            "sent_at": fecha_ingreso,
            "shift_label": "Registro inicial",
            "unit": data.get("sala", ""),
            "vitals": vitals,
            "note": note,
            "raw_header": f"Registro de paciente - {data.get('tipo_clasificacion', 'Ingreso')}",
            "summary_shift": ""
        }
        
        # Crear paciente completo
        new_patient = {
            "patient_id": patient_id,
            "patient": f"{data.get('apellido', '')}, {data.get('nombre', '')}".strip(", "),
            "nickname": data.get("nombre", ""),
            "dni": data.get("dni", ""),
            "historia_clinica": data.get("historia_clinica", ""),
            "edad": data.get("edad", ""),
            "sala": data.get("sala", ""),
            "cama": data.get("cama", ""),
            "diagnostico_principal": data.get("diagnostico_principal", ""),
            "antecedentes": data.get("antecedentes", ""),
            "tipo_clasificacion": data.get("tipo_clasificacion", "Ingreso"),  # Ingresos, Controles, Pase a UCIP, etc.
            "fecha_ingreso": fecha_ingreso,
            "motivo_consulta": motivo_consulta,
            "embarazo": data.get("embarazo", False),
            "shifts": [initial_shift],
            "shiftsPharmacy": [],
            "triage_inicial": triage_result,
            "risk_inicial": risk_profile,
            "triage_auditoria": {
                "sugerido_por_sistema": triage_sugerido,
                "nivel_final": triage_result.get("level"),
                "modificado": triage_result.get("modificado_por_profesional", False),
                "justificacion": triage_result.get("justificacion_modificacion", ""),
                "profesional": data.get("profesional_registro", "Sistema"),
                "timestamp": datetime.now().isoformat()
            }
        }
        
        # Agregar paciente a la lista
        existing_patients.append(new_patient)
        updated_data = {"patients": existing_patients}
        
        # Crear backup
        backup_path = DATA_PATH + ".backup"
        if os.path.exists(DATA_PATH):
            try:
                import shutil
                shutil.copy2(DATA_PATH, backup_path)
            except:
                pass
        
        # Guardar datos
        with open(DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(updated_data, f, ensure_ascii=False, indent=2)
        
        # Invalidar cache
        _DATA_CACHE["data"] = None
        _DATA_CACHE["mtime"] = None
        
        return jsonify({
            "success": True,
            "message": "Paciente registrado exitosamente",
            "patient": {
                "patient_id": patient_id,
                "patient": new_patient["patient"],
                "triage": triage_result,
                "risk": risk_profile
            }
        })
        
    except Exception as e:
        return jsonify({"error": f"Error al registrar paciente: {str(e)}"}), 500


@app.route("/api/upload_word", methods=["POST"])
def upload_word():
    """Endpoint para subir y procesar documentos Word de pase de guardia"""
    if 'file' not in request.files:
        return jsonify({"error": "No se proporcionó archivo"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No se seleccionó archivo"}), 400
    
    if not allowed_file(file.filename):
        return jsonify({"error": "Tipo de archivo no permitido. Solo se aceptan .docx o .doc"}), 400
    
    try:
        # Guardar archivo temporalmente
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Parsear documento
        parsed_data = word_parser.parse_word_document(filepath)
        converted_data = word_parser.convert_to_system_format(parsed_data)
        
        # Cargar datos existentes
        existing_data = load_data()
        existing_patients = existing_data.get("patients", [])
        
        # Fusionar pacientes (evitar duplicados por patient_id o nombre + DNI)
        existing_ids = {p.get("patient_id") for p in existing_patients if p.get("patient_id")}
        new_patients = []
        updated_count = 0
        
        def normalize_name(name_str):
            """Normaliza nombre para comparación"""
            if not name_str:
                return ""
            # Si tiene formato "Apellido, Nombre", extraer ambos
            if "," in name_str:
                parts = name_str.split(",", 1)
                return {
                    "apellido": parts[0].strip().lower(),
                    "nombre": parts[1].strip().lower() if len(parts) > 1 else ""
                }
            else:
                # Si solo tiene nombre, intentar separar
                parts = name_str.strip().split()
                if len(parts) >= 2:
                    return {
                        "nombre": parts[0].lower(),
                        "apellido": " ".join(parts[1:]).lower()
                    }
                return {
                    "nombre": name_str.strip().lower(),
                    "apellido": ""
                }
        
        def find_existing_patient(new_patient):
            """Busca paciente existente por patient_id o nombre + DNI"""
            new_pid = new_patient.get("patient_id")
            new_nombre = (new_patient.get("nombre", "") or "").strip().lower()
            new_apellido = (new_patient.get("apellido", "") or "").strip().lower()
            new_dni = (new_patient.get("dni", "") or "").strip()
            
            for i, existing_patient in enumerate(existing_patients):
                # Verificar por patient_id
                if new_pid and existing_patient.get("patient_id") == new_pid:
                    return i
                
                # Verificar por nombre + DNI
                existing_dni = (existing_patient.get("dni", "") or "").strip()
                existing_name_info = normalize_name(existing_patient.get("patient", ""))
                existing_nickname = (existing_patient.get("nickname", "") or "").strip().lower()
                
                # Usar nickname si está disponible, sino usar el nombre del campo patient
                existing_nombre = existing_nickname or existing_name_info.get("nombre", "")
                existing_apellido = existing_name_info.get("apellido", "")
                
                # Comparar nombre + apellido + DNI
                if new_dni and existing_dni and new_dni == existing_dni:
                    if new_nombre == existing_nombre and new_apellido == existing_apellido:
                        return i
                
                # Si no hay DNI en ninguno, comparar solo por nombre + apellido
                if not new_dni and not existing_dni:
                    if new_nombre == existing_nombre and new_apellido == existing_apellido:
                        return i
            
            return None
        
        for new_patient in converted_data.get("patients", []):
            pid = new_patient.get("patient_id")
            existing_idx = find_existing_patient(new_patient)
            
            if existing_idx is None:
                # Paciente nuevo
                if pid:
                    new_patients.append(new_patient)
                    existing_ids.add(pid)
            else:
                # Paciente existente - fusionar información
                existing_patient = existing_patients[existing_idx]
                
                # Fusionar shifts
                existing_shifts = existing_patient.get("shifts", [])
                new_shifts = new_patient.get("shifts", [])
                existing_patients[existing_idx]["shifts"] = existing_shifts + new_shifts
                
                # Fusionar pharmacy
                existing_pharmacy = existing_patient.get("shiftsPharmacy", [])
                new_pharmacy = new_patient.get("shiftsPharmacy", [])
                existing_patients[existing_idx]["shiftsPharmacy"] = existing_pharmacy + new_pharmacy
                
                # Actualizar DNI si el nuevo tiene DNI y el existente no
                if not existing_patient.get("dni") and new_patient.get("dni"):
                    existing_patients[existing_idx]["dni"] = new_patient.get("dni")
                
                updated_count += 1
        
        # Agregar nuevos pacientes
        all_patients = existing_patients + new_patients
        
        # Guardar datos actualizados en residentes_db.json
        updated_data = {
            "patients": all_patients
        }
        
        # Crear backup antes de guardar (solo si hay cambios)
        if len(new_patients) > 0 or updated_count > 0:
            backup_path = DATA_PATH + ".backup"
            if os.path.exists(DATA_PATH):
                try:
                    import shutil
                    shutil.copy2(DATA_PATH, backup_path)
                except:
                    pass
            
            # Guardar datos actualizados
            try:
                with open(DATA_PATH, "w", encoding="utf-8") as f:
                    json.dump(updated_data, f, ensure_ascii=False, indent=2)
                
                # Invalidar cache para que se recargue
                _DATA_CACHE["data"] = None
                _DATA_CACHE["mtime"] = None
                
            except Exception as save_error:
                # Limpiar archivo temporal
                try:
                    os.remove(filepath)
                except:
                    pass
                return jsonify({"error": f"Error al guardar datos en {DATA_PATH}: {str(save_error)}"}), 500
        
        # Limpiar archivo temporal
        try:
            os.remove(filepath)
        except:
            pass
        
        return jsonify({
            "success": True,
            "message": f"Documento procesado exitosamente",
            "data": converted_data,
            "stats": {
                "pacientes_nuevos": len(new_patients),
                "pacientes_existentes_actualizados": updated_count,
                "total_procesados": len(converted_data.get("patients", [])),
                "meta": converted_data.get("meta", {})
            }
        })
        
    except Exception as e:
        # Limpiar archivo en caso de error
        try:
            if 'filepath' in locals():
                os.remove(filepath)
        except:
            pass
        
        return jsonify({"error": f"Error al procesar documento: {str(e)}"}), 500


@app.route("/api/upload_image", methods=["POST"])
def upload_image():
    """Endpoint para subir y procesar imagen de ingreso médico usando IA"""
    if 'file' not in request.files:
        return jsonify({"error": "No se proporcionó archivo"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No se seleccionó archivo"}), 400
    
    if not allowed_image_file(file.filename):
        return jsonify({"error": "Tipo de archivo no permitido. Solo se aceptan imágenes (jpg, jpeg, png, gif, bmp, webp)"}), 400
    
    if not gemini_service:
        return jsonify({"error": "Servicio de IA no disponible"}), 500
    
    try:
        # Guardar archivo temporalmente
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Extraer datos usando Gemini Vision
        extracted_data = gemini_service.extract_patient_data_from_image(filepath)
        
        # Limpiar archivo temporal
        try:
            os.remove(filepath)
        except:
            pass
        
        # Validar que se extrajo al menos nombre o apellido
        if not extracted_data.get("nombre") and not extracted_data.get("apellido"):
            return jsonify({
                "error": "No se pudieron extraer datos suficientes de la imagen. Por favor verifique que la imagen contenga información legible del paciente.",
                "extracted_data": extracted_data
            }), 400
        
        return jsonify({
            "success": True,
            "message": "Imagen procesada exitosamente",
            "extracted_data": extracted_data
        })
        
    except Exception as e:
        # Limpiar archivo en caso de error
        try:
            if 'filepath' in locals():
                os.remove(filepath)
        except:
            pass
        
        error_msg = str(e)
        if "Rate limit" in error_msg:
            return jsonify({"error": "El servicio de IA está temporalmente sobrecargado. Por favor intente nuevamente en unos momentos."}), 429
        
        return jsonify({"error": f"Error al procesar imagen: {error_msg}"}), 500


@app.route("/api/upload_image_complete", methods=["POST"])
def upload_image_complete():
    """Endpoint para subir y procesar imagen(es) de ingreso completo pediátrico usando IA (sin triage)"""
    import time
    start_time = time.time()
    
    # Verificar si hay archivos (puede ser 'file' o 'files')
    files = []
    if 'files' in request.files:
        # Múltiples archivos
        files_list = request.files.getlist('files')
        files = [f for f in files_list if f.filename != '']
    elif 'file' in request.files:
        # Un solo archivo (compatibilidad hacia atrás)
        file = request.files['file']
        if file.filename != '':
            files = [file]
    
    if len(files) == 0:
        return jsonify({"error": "No se proporcionaron archivos"}), 400
    
    print(f"[Upload Image Complete] Procesando {len(files)} imagen(es)...")
    
    # Validar todos los archivos
    for file in files:
        if not allowed_image_file(file.filename):
            return jsonify({"error": f"Tipo de archivo no permitido: {file.filename}. Solo se aceptan imágenes (jpg, jpeg, png, gif, bmp, webp)"}), 400
    
    if not gemini_service:
        return jsonify({"error": "Servicio de IA no disponible"}), 500
    
    filepaths = []
    try:
        # Guardar archivos temporalmente
        for file in files:
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            filepaths.append(filepath)
        
        print(f"[Upload Image Complete] Archivos guardados. Iniciando extracción con Gemini...")
        extraction_start = time.time()
        
        # Extraer datos completos usando Gemini Vision (puede procesar múltiples imágenes)
        try:
            if len(filepaths) == 1:
                extracted_data = gemini_service.extract_complete_admission_data_from_image(filepaths[0])
            else:
                print(f"[Upload Image Complete] Procesando {len(filepaths)} imágenes (esto puede tomar varios minutos)...")
                extracted_data = gemini_service.extract_complete_admission_data_from_images(filepaths)
            
            extraction_time = time.time() - extraction_start
            print(f"[Upload Image Complete] ✓ Extracción completada en {extraction_time:.2f} segundos")
            
        except Exception as extraction_error:
            extraction_time = time.time() - extraction_start
            error_str = str(extraction_error)
            
            # Detectar diferentes tipos de errores
            if "timeout" in error_str.lower() or "timed out" in error_str.lower():
                print(f"[Upload Image Complete] ✗ Timeout después de {extraction_time:.2f} segundos")
                raise Exception(f"El procesamiento de las imágenes tomó demasiado tiempo ({extraction_time:.0f} segundos). Por favor, intente con imágenes más pequeñas o procese menos imágenes a la vez.")
            elif "429" in error_str or "Resource exhausted" in error_str or "Rate limit" in error_str:
                print(f"[Upload Image Complete] ✗ Rate limit después de {extraction_time:.2f} segundos")
                raise Exception("El servicio de IA está temporalmente sobrecargado. Por favor intente nuevamente en unos momentos.")
            else:
                print(f"[Upload Image Complete] ✗ Error después de {extraction_time:.2f} segundos: {error_str[:200]}")
                raise
        
        # Limpiar archivos temporales
        for filepath in filepaths:
            try:
                os.remove(filepath)
            except:
                pass
        
        # Validar que se extrajo al menos información básica del paciente
        seccion_a = extracted_data.get("seccion_a", {}) if isinstance(extracted_data, dict) else {}
        campos_ilegibles = extracted_data.get("campos_ilegibles", []) if isinstance(extracted_data, dict) else []
        
        # Log de los datos extraídos para debugging
        print(f"[Upload Image Complete] Datos extraídos - Sección A: {seccion_a}")
        print(f"[Upload Image Complete] Campos ilegibles: {campos_ilegibles}")
        
        apellido_nombre = seccion_a.get("apellido_nombre_nino") if isinstance(seccion_a, dict) else None
        
        if not apellido_nombre or (isinstance(apellido_nombre, str) and not apellido_nombre.strip()):
            print(f"[Upload Image Complete] ✗ Validación fallida: apellido_nombre_nino no encontrado o vacío")
            print(f"[Upload Image Complete] Datos completos recibidos: {json.dumps(extracted_data, indent=2, ensure_ascii=False)[:500]}")
            return jsonify({
                "error": "No se pudieron extraer datos suficientes de la imagen. Por favor verifique que la imagen contenga información legible del formulario de ingreso.",
                "extracted_data": extracted_data,
                "campos_ilegibles": campos_ilegibles,
                "debug": {
                    "seccion_a_keys": list(seccion_a.keys()) if isinstance(seccion_a, dict) else [],
                    "apellido_nombre_value": apellido_nombre
                }
            }), 400
        
        # Preparar mensaje con información sobre campos ilegibles
        message = "Imagen procesada exitosamente"
        if campos_ilegibles and len(campos_ilegibles) > 0:
            message += f". Se identificaron {len(campos_ilegibles)} campo(s) que no pudieron ser interpretados completamente."
        
        total_time = time.time() - start_time
        print(f"[Upload Image Complete] ✓ Proceso completo en {total_time:.2f} segundos")
        
        return jsonify({
            "success": True,
            "message": message,
            "extracted_data": extracted_data,
            "campos_ilegibles": campos_ilegibles,
            "total_campos_ilegibles": len(campos_ilegibles) if campos_ilegibles else 0
        })
        
    except Exception as e:
        # Limpiar archivos temporales en caso de error
        try:
            if 'filepaths' in locals():
                for filepath in filepaths:
                    try:
                        os.remove(filepath)
                    except:
                        pass
        except:
            pass
        
        total_time = time.time() - start_time
        error_msg = str(e)
        
        # Log del error completo
        import traceback
        print(f"[Upload Image Complete] ✗ Error después de {total_time:.2f} segundos:")
        print(f"  Mensaje: {error_msg}")
        traceback.print_exc()
        
        # Mensajes de error más específicos
        if "Rate limit" in error_msg or "sobrecargado" in error_msg:
            return jsonify({"error": "El servicio de IA está temporalmente sobrecargado. Por favor intente nuevamente en unos momentos."}), 429
        elif "timeout" in error_msg.lower() or "demasiado tiempo" in error_msg.lower():
            return jsonify({"error": error_msg}), 504  # Gateway Timeout
        else:
            return jsonify({"error": f"Error al procesar imagen: {error_msg}"}), 500


@app.route("/api/patients/register_complete", methods=["POST"])
def register_complete_patient():
    """
    Endpoint para registrar un paciente con ingreso completo pediátrico (sin triage).
    Este endpoint guarda todos los datos del formulario completo pero NO calcula triage.
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No se proporcionaron datos"}), 400
        
        # Extraer datos básicos del paciente desde el formulario
        apellido_nombre = data.get("apellido_nombre_nino", "")
        
        if not apellido_nombre:
            return jsonify({"error": "Se requiere el nombre del paciente"}), 400
        
        # Generar ID único para el paciente
        import uuid
        historia_clinica = data.get("historia_clinica", "")
        if historia_clinica:
            patient_id = f"HC-{historia_clinica}"
        else:
            patient_id = f"HC-{uuid.uuid4().hex[:8].upper()}"
        
        # Cargar datos existentes
        existing_data = load_data()
        existing_patients = existing_data.get("patients", [])
        
        # Verificar si el paciente ya existe
        for p in existing_patients:
            if p.get("patient_id") == patient_id or p.get("patient") == apellido_nombre:
                return jsonify({"error": f"El paciente {apellido_nombre} ya existe en el sistema"}), 400
        
        # Crear estructura del paciente con ingreso completo
        fecha_ingreso = datetime.now().isoformat()
        
        # Extraer nombre y apellido si están separados
        nombre_parts = apellido_nombre.split(",", 1)
        if len(nombre_parts) == 2:
            apellido = nombre_parts[0].strip()
            nombre = nombre_parts[1].strip()
        else:
            # Intentar separar por espacios
            parts = apellido_nombre.strip().split()
            if len(parts) >= 2:
                apellido = " ".join(parts[:-1])
                nombre = parts[-1]
            else:
                apellido = ""
                nombre = apellido_nombre.strip()
        
        patient_name = f"{apellido}, {nombre}".strip(", ")
        
        # Construir objeto con todos los datos del formulario completo
        complete_data = {
            "seccion_a": {
                "apellido_nombre_nino": apellido_nombre,
                "sexo": data.get("sexo"),
                "fecha_nacimiento": data.get("fecha_nacimiento"),
                "apellido_nombre_madre": data.get("apellido_nombre_madre"),
                "edad_madre": data.get("edad_madre"),
                "apellido_nombre_padre": data.get("apellido_nombre_padre"),
                "edad_padre": data.get("edad_padre"),
                "numero_afiliado": data.get("numero_afiliado"),
                "mutual": data.get("mutual"),
                "telefono": data.get("telefono"),
                "domicilio": data.get("domicilio"),
                "medico_caso": data.get("medico_caso"),
                "historia_clinica": historia_clinica,
                "cuidados_intermedios": data.get("cuidados_intermedios")
            },
            "seccion_b": {
                "motivo_consulta": data.get("motivo_consulta"),
                "evolucion_tratamientos": data.get("evolucion_tratamientos")
            },
            "seccion_c": {
                "tbc": data.get("tbc", False),
                "chagas": data.get("chagas", False),
                "alergia": data.get("alergia", False),
                "sifilis": data.get("sifilis", False),
                "diabetes": data.get("diabetes", False),
                "enf_sangre": data.get("enf_sangre", False),
                "enf_neurologicas": data.get("enf_neurologicas", False),
                "antecedentes_familiares": data.get("antecedentes_familiares")
            },
            "seccion_d": {
                "embarazo_parto": {
                    "evolucion_embarazo": data.get("evolucion_embarazo"),
                    "drogas": data.get("drogas"),
                    "rx": data.get("rx"),
                    "ruptura_bolsa": data.get("ruptura_bolsa"),
                    "peso_nacimiento": data.get("peso_nacimiento"),
                    "reanimacion": data.get("reanimacion", False),
                    "cianosis": data.get("cianosis", False),
                    "ictericia": data.get("ictericia", False),
                    "convulsiones": data.get("convulsiones", False),
                    "otras": data.get("otras_neonatales")
                },
                "nutricion": data.get("nutricion"),
                "antecedentes_psicomotores": {
                    "sonrisa_social": data.get("sonrisa_social"),
                    "sosten_cabeza": data.get("sosten_cabeza"),
                    "sentado": data.get("sentado"),
                    "camina": data.get("camina"),
                    "palabras": data.get("palabras"),
                    "frases": data.get("frases"),
                    "control_esfinter": data.get("control_esfinter"),
                    "escuela_grado": data.get("escuela_grado"),
                    "escuela_problemas": data.get("escuela_problemas")
                },
                "inmunizaciones": data.get("inmunizaciones")
            },
            "seccion_e": {
                "antecedentes_patologicos": data.get("antecedentes_patologicos")
            },
            "seccion_f": {
                "datos_socioeconomicos": data.get("datos_socioeconomicos")
            },
            "examen_fisico": {
                "fecha": data.get("examen_fecha"),
                "hora": data.get("examen_hora"),
                "edad": data.get("examen_edad"),
                "peso": data.get("peso"),
                "talla": data.get("talla"),
                "perimetro_cefalico": data.get("perimetro_cefalico"),
                "temp_rectal": data.get("temp_rectal"),
                "temp_axilar": data.get("temp_axilar"),
                "aspecto_general": data.get("aspecto_general"),
                "psiquismo": {
                    "normal": data.get("psiquismo_normal", False),
                    "inquietud": data.get("psiquismo_inquietud", False),
                    "delirio": data.get("psiquismo_delirio", False),
                    "obnubilado": data.get("psiquismo_obnubilado", False),
                    "estupor": data.get("psiquismo_estupor", False),
                    "coma": data.get("psiquismo_coma", False),
                    "grado": data.get("psiquismo_grado")
                },
                "decubito": {
                    "dorsal": data.get("decubito_dorsal", False),
                    "ventral": data.get("decubito_ventral", False),
                    "indiferente": data.get("decubito_indiferente", False),
                    "activo": data.get("decubito_activo", False),
                    "pasivo": data.get("decubito_pasivo", False),
                    "obligado": data.get("decubito_obligado", False)
                },
                "piel": {
                    "cianosis": data.get("piel_cianosis", False),
                    "palidez": data.get("piel_palidez", False),
                    "ictericia": data.get("piel_ictericia", False),
                    "signo_pliegue": data.get("piel_signo_pliegue", False),
                    "humedad": data.get("piel_humedad", False),
                    "turgor": data.get("piel_turgor", False),
                    "elasticidad": data.get("piel_elasticidad", False),
                    "otros": data.get("piel_otros", "")
                },
                "tcs": {
                    "paniculo_adiposo": {
                        "conservado": data.get("tcs_paniculo_adiposo_conservado", False),
                        "disminuido": data.get("tcs_paniculo_adiposo_disminuido", False),
                        "aumentado": data.get("tcs_paniculo_adiposo_aumentado", False)
                    },
                    "edemas": data.get("tcs_edemas", ""),
                    "ganglios": data.get("tcs_ganglios", "")
                },
                "cabeza": {
                    "craneo": {
                        "forma": data.get("cabeza_craneo_forma", ""),
                        "facies": data.get("cabeza_facies", "")
                    },
                    "fontanela": {
                        "tamano": data.get("cabeza_fontanela_tamano", ""),
                        "tension": data.get("cabeza_fontanela_tension", "")
                    },
                    "ojos": {
                        "enoftalmos": data.get("cabeza_ojos_enoftalmos", False),
                        "exoftalmos": data.get("cabeza_ojos_exoftalmos", False),
                        "pupilas_miosis": data.get("cabeza_ojos_pupilas_miosis", False),
                        "pupilas_midriasis": data.get("cabeza_ojos_pupilas_midriasis", False),
                        "otras": data.get("cabeza_ojos_otras", ""),
                        "rfm": data.get("cabeza_ojos_rfm", False),
                        "estrabismo": data.get("cabeza_ojos_estrabismo", False),
                        "conjuntivas": data.get("cabeza_ojos_conjuntivas", "")
                    },
                    "oidos": {
                        "pabellones": data.get("cabeza_oidos_pabellones", ""),
                        "p_trago": data.get("cabeza_oidos_p_trago", False),
                        "otoscopia": data.get("cabeza_oidos_otoscopia", "")
                    },
                    "boca": data.get("cabeza_boca", ""),
                    "nariz": data.get("cabeza_nariz", ""),
                    "cuello": data.get("cabeza_cuello", "")
                },
                "observaciones": data.get("observaciones_examen")
            },
            "presuncion_diagnostico": data.get("presuncion_diagnostico"),
            "plan_estudio_tratamiento": data.get("plan_estudio_tratamiento")
        }
        
        new_patient = {
            "patient_id": patient_id,
            "patient": patient_name,
            "fecha_ingreso": fecha_ingreso,
            "tipo_ingreso": "completo",  # Marcar como ingreso completo
            "complete_admission_data": complete_data,  # Guardar todos los datos del formulario completo
            "shifts": [],  # Sin shifts iniciales
            "triage_inicial": None,  # Sin triage inicial (ya se hizo antes)
            "risk_inicial": None
        }
        
        # Agregar paciente
        existing_patients.append(new_patient)
        existing_data["patients"] = existing_patients
        
        # Guardar datos
        save_data(existing_data)
        
        print(f"[Register Complete] ✓ Paciente registrado: {patient_name} ({patient_id})")
        
        return jsonify({
            "success": True,
            "message": "Paciente registrado exitosamente",
            "patient": {
                "patient_id": patient_id,
                "patient": patient_name,
                "tipo_ingreso": "completo"
            }
        })
        
    except Exception as e:
        error_msg = str(e)
        print(f"[Register Complete] ✗ Error: {error_msg}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Error al registrar paciente: {error_msg}"}), 500


def extract_vitals_from_text(text):
    """Extrae signos vitales del texto usando expresiones regulares"""
    if not text:
        return {}
    
    vitals = {}
    text_lower = text.lower()
    
    # TA (Tensión Arterial): "TA 120/80" o "ta 120/80"
    ta_match = re.search(r'\bta\s*[:=]?\s*(\d+)\s*[/-]\s*(\d+)', text_lower)
    if ta_match:
        vitals["ta_sis"] = int(ta_match.group(1))
        vitals["ta_dia"] = int(ta_match.group(2))
    
    # SAT (Saturación): "SAT 95" o "sat 95" o "SAT 95/98"
    sat_match = re.search(r'\bsat\s*[:=]?\s*(\d+)', text_lower)
    if sat_match:
        vitals["sat"] = int(sat_match.group(1))
    
    # TEMP (Temperatura): "TEMP 37" o "t 37" o "t°37"
    temp_match = re.search(r'\b(?:temp|t|temperatura)\s*[:=°]?\s*(\d+(?:[.,]\d+)?)', text_lower)
    if temp_match:
        temp_str = temp_match.group(1).replace(',', '.')
        try:
            vitals["temp_c"] = float(temp_str)
        except:
            pass
    
    # FC (Frecuencia Cardíaca): "FC 80" o "fc 80"
    fc_match = re.search(r'\bfc\s*[:=]?\s*(\d+)', text_lower)
    if fc_match:
        vitals["fc"] = int(fc_match.group(1))
    
    # FR (Frecuencia Respiratoria): "FR 20" o "fr 20"
    fr_match = re.search(r'\bfr\s*[:=]?\s*(\d+)', text_lower)
    if fr_match:
        vitals["fr"] = int(fr_match.group(1))
    
    # DIU (Diuresis): "DIU 1000" o "diu 1000"
    diu_match = re.search(r'\bdiu\s*[:=]?\s*(\d+)', text_lower)
    if diu_match:
        vitals["diu"] = int(diu_match.group(1))
    
    # Glucosa: "GLU 200" o "glucosa 200" o "GPD 200"
    glu_match = re.search(r'\b(?:glu|glucosa|gpd|gpc)\s*[:=]?\s*(\d+)', text_lower)
    if glu_match:
        vitals["glucosa"] = int(glu_match.group(1))
    
    return vitals


@app.route("/api/load_whatsapp_chat", methods=["POST"])
def load_whatsapp_chat():
    """Endpoint para cargar y procesar archivo de WhatsApp de grupos"""
    try:
        # Buscar el archivo en la raíz del proyecto
        chat_filename = "Chat de WhatsApp con Planta Alta Planta Baja.txt"
        chat_path = os.path.join(BASE_DIR, chat_filename)
        
        if not os.path.exists(chat_path):
            return jsonify({
                "error": f"No se encontró el archivo '{chat_filename}' en la raíz del proyecto"
            }), 404
        
        print(f"[WhatsApp Chat] Procesando archivo: {chat_path}")
        
        # Leer y procesar el archivo
        patients_found = []
        current_message = None
        current_sender = None
        current_date = None
        
        # Patrón para detectar líneas de mensaje: "d/m/aaaa, hh:mm - Remitente: Mensaje"
        message_pattern = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4}), (\d{1,2}):(\d{2}) - ([^:]+): (.*)$")
        
        # Patrón para detectar nombres de pacientes (líneas que empiezan con apellido y nombre)
        patient_pattern = re.compile(r"^([A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]+(?:\s+[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]+)*)\s*([A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]+(?:\s+[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]+)*)\s*[:–-]")
        
        with open(chat_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                # Intentar detectar si es un mensaje nuevo
                msg_match = message_pattern.match(line)
                if msg_match:
                    # Guardar mensaje anterior si tenía información de paciente
                    if current_message and current_sender:
                        # Procesar mensaje anterior para extraer pacientes
                        text_lines = current_message.split('\n')
                        for text_line in text_lines:
                            patient_match = patient_pattern.match(text_line.strip())
                            if patient_match:
                                apellido = patient_match.group(1)
                                nombre = patient_match.group(2)
                                patient_name = f"{apellido}, {nombre}"
                                
                                # Extraer información adicional de la línea
                                info = text_line[patient_match.end():].strip()
                                
                                patients_found.append({
                                    "nombre": patient_name,
                                    "info": info,
                                    "fecha": current_date,
                                    "remitente": current_sender
                                })
                    
                    # Nuevo mensaje
                    day, month, year = int(msg_match.group(1)), int(msg_match.group(2)), int(msg_match.group(3))
                    hour, minute = int(msg_match.group(4)), int(msg_match.group(5))
                    current_sender = msg_match.group(6).strip()
                    current_message = msg_match.group(7)
                    current_date = f"{day:02d}/{month:02d}/{year}"
                else:
                    # Continuación del mensaje
                    if current_message is not None:
                        current_message += "\n" + line
        
        # Procesar último mensaje
        if current_message and current_sender:
            text_lines = current_message.split('\n')
            for text_line in text_lines:
                patient_match = patient_pattern.match(text_line.strip())
                if patient_match:
                    apellido = patient_match.group(1)
                    nombre = patient_match.group(2)
                    patient_name = f"{apellido}, {nombre}"
                    info = text_line[patient_match.end():].strip()
                    
                    patients_found.append({
                        "nombre": patient_name,
                        "info": info,
                        "fecha": current_date,
                        "remitente": current_sender
                    })
        
        # Cargar datos existentes
        existing_data = load_data()
        existing_patients = existing_data.get("patients", [])
        
        # Agregar pacientes encontrados a la base de datos (evitar duplicados)
        added_count = 0
        updated_count = 0
        existing_names = {p.get("patient", "").lower() for p in existing_patients}
        
        for patient_info in patients_found:
            patient_name = patient_info["nombre"]
            patient_name_lower = patient_name.lower()
            
            # Buscar si el paciente ya existe
            existing_patient = None
            for p in existing_patients:
                if p.get("patient", "").lower() == patient_name_lower:
                    existing_patient = p
                    break
            
            if existing_patient:
                # Actualizar paciente existente - agregar shift si no existe
                if "shifts" not in existing_patient:
                    existing_patient["shifts"] = []
                
                # Convertir fecha DD/MM/YYYY a formato ISO para sent_at
                fecha_str = patient_info.get("fecha", "")
                sent_at = None
                if fecha_str:
                    try:
                        # Parsear fecha DD/MM/YYYY
                        parts = fecha_str.split("/")
                        if len(parts) == 3:
                            day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
                            sent_at = datetime(year, month, day).isoformat()
                    except:
                        pass
                
                # Extraer vitals del texto
                info_text = patient_info.get("info", "")
                vitals = extract_vitals_from_text(info_text)
                
                # Crear un shift básico con la información encontrada en formato correcto
                shift_data = {
                    "sent_at": sent_at or datetime.now().isoformat(),
                    "posted_by": patient_info.get("remitente", "WhatsApp"),
                    "shift_label": "Guardia",
                    "unit": patient_info.get("remitente", "Planta Alta/Planta Baja"),
                    "note": info_text,
                    "text": info_text,  # Mantener compatibilidad
                    "sender": patient_info.get("remitente", ""),  # Mantener compatibilidad
                    "datetime": fecha_str,  # Mantener compatibilidad
                    "vitals": vitals  # Agregar vitals extraídos
                }
                
                # Verificar si el shift ya existe para evitar duplicados
                shift_exists = any(
                    (s.get("sent_at") == shift_data["sent_at"] or s.get("datetime") == fecha_str) and 
                    (s.get("posted_by") == shift_data["posted_by"] or s.get("sender") == patient_info.get("remitente", ""))
                    for s in existing_patient["shifts"]
                )
                
                if not shift_exists:
                    existing_patient["shifts"].append(shift_data)
                    updated_count += 1
            else:
                # Nuevo paciente
                patient_id = f"HC-{len(existing_patients) + 1:05d}"
                
                # Convertir fecha DD/MM/YYYY a formato ISO
                fecha_str = patient_info.get("fecha", "")
                fecha_ingreso_iso = datetime.now().strftime("%Y-%m-%d")
                sent_at = None
                if fecha_str:
                    try:
                        parts = fecha_str.split("/")
                        if len(parts) == 3:
                            day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
                            fecha_ingreso_iso = f"{year}-{month:02d}-{day:02d}"
                            sent_at = datetime(year, month, day).isoformat()
                    except:
                        pass
                
                # Extraer vitals del texto
                info_text = patient_info.get("info", "")
                vitals = extract_vitals_from_text(info_text)
                
                new_patient = {
                    "patient_id": patient_id,
                    "patient": patient_name,
                    "fecha_ingreso": fecha_ingreso_iso,
                    "tipo_ingreso": "whatsapp",
                    "shifts": [{
                        "sent_at": sent_at or datetime.now().isoformat(),
                        "posted_by": patient_info.get("remitente", "WhatsApp"),
                        "shift_label": "Guardia",
                        "unit": patient_info.get("remitente", "Planta Alta/Planta Baja"),
                        "note": info_text,
                        "text": info_text,  # Mantener compatibilidad
                        "sender": patient_info.get("remitente", ""),  # Mantener compatibilidad
                        "datetime": fecha_str,  # Mantener compatibilidad
                        "vitals": vitals  # Agregar vitals extraídos
                    }],
                    "shiftsPharmacy": []
                }
                existing_patients.append(new_patient)
                added_count += 1
        
        # Guardar datos
        existing_data["patients"] = existing_patients
        save_data(existing_data)
        
        print(f"[WhatsApp Chat] ✓ Procesado: {len(patients_found)} referencias encontradas, {added_count} pacientes nuevos, {updated_count} pacientes actualizados")
        
        return jsonify({
            "success": True,
            "message": f"Archivo procesado exitosamente. {added_count} pacientes nuevos, {updated_count} pacientes actualizados",
            "stats": {
                "referencias_encontradas": len(patients_found),
                "pacientes_nuevos": added_count,
                "pacientes_actualizados": updated_count
            },
            "pacientes": patients_found[:10]  # Mostrar primeros 10 como muestra
        })
        
    except Exception as e:
        error_msg = str(e)
        print(f"[WhatsApp Chat] ✗ Error: {error_msg}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Error al procesar archivo de WhatsApp: {error_msg}"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)