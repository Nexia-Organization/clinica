import os
import json
import re
import argparse
import zlib
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from typing import List, Dict, Any, Tuple, Optional


CHAT_FILENAME = "Chat de WhatsApp con Farmacia. Indic médicas11-2025.txt"
NAMES_ID_FILENAME = "names-id.txt"


def parse_whatsapp_chat(path: str) -> List[Dict[str, Any]]:
    """
    Parsea un chat exportado de WhatsApp al formato:
    "d/m/aaaa, hh:mm - Remitente: Mensaje"
    Soporta mensajes multilínea.
    """
    messages: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    # Ejemplo de línea:
    # 1/11/2025, 09:55 - ENCARGADOS A: Tinta: omeprazol 8 y 20 hs por 15 dias
    line_re = re.compile(
        r"^(\d{1,2})/(\d{1,2})/(\d{4}), (\d{1,2}):(\d{2}) - ([^:]+): (.*)$"
    )

    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            m = line_re.match(line)
            if m:
                # Cierra mensaje anterior si lo hubiera
                if current is not None:
                    messages.append(current)

                day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
                hour, minute = int(m.group(4)), int(m.group(5))
                sender = m.group(6).strip()
                text = m.group(7)

                dt = datetime(year, month, day, hour, minute)
                current = {
                    "datetime": dt,
                    "sender": sender,
                    "text": text,
                }
            else:
                # Continuación de mensaje multilínea
                if current is not None:
                    # Conservamos saltos de línea para no perder información
                    current["text"] += "\n" + line
                else:
                    # Línea suelta sin cabecera de fecha: se ignora
                    continue

    if current is not None:
        messages.append(current)

    return messages


def load_patients_config(path: str) -> List[Dict[str, Any]]:
    """
    Carga la lista de pacientes desde el JSON existente.
    Este JSON es el que ya generaba tu proyecto con IA, y se usa
    únicamente para tomar los name-id (y nickname) y el formato base.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("El archivo de pacientes debe ser una lista JSON.")

    return data


def infer_patients_from_chat(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Inferimos pacientes directamente del chat sin depender de ningún JSON externo.

    Estrategia (para evitar falsos positivos tipo "Ahora", "Acemuk", etc.):
    - Primero detectamos nombres explícitos con patrón "Paciente: ..." al inicio del mensaje.
    - Con esa lista de pacientes, armamos tokens y luego asignamos mensajes aunque
      el mensaje no tenga ":" (ej: "Abud julio traer ..."), siempre que el inicio
      coincida con un token de un paciente ya detectado.

    Genera IDs determinísticos en formato "pat_########" basado en el nombre.
    """
    # Ej: "Tinta: ..." / "Rodriguez Alicia: ..." / "Abud Julio: ..."
    name_colon_pattern = re.compile(
        r"^([A-Za-zÁÉÍÓÚÑáéíóúñ]+(?:\s+[A-Za-zÁÉÍÓÚÑáéíóúñ]+){0,2})\s*:\s+(.+)$"
    )

    # Ej: líneas que son solo un nombre/apellido (típico en mensajes multi-paciente)
    # "Brumati" / "Emma perez" / "Alperin"
    name_alone_pattern = re.compile(
        r"^([A-Za-zÁÉÍÓÚÑáéíóúñ]+(?:\s+[A-Za-zÁÉÍÓÚÑáéíóúñ]+){0,2})$"
    )

    # Palabras que NO deberían formar parte de un nombre de paciente
    stopword_words = {
        "buen", "buenas", "dia", "día", "dias", "días", "tarde", "tardes", "noche", "noches",
        "hola", "gracias", "ok", "perfecto", "listo", "si", "sí", "no",
        "para", "por", "porque", "y", "el", "la", "los", "las", "un", "una",
        "necesito", "necesitamos", "pedir", "pide", "piden", "avisar", "avisen",
        "preparar", "preparen", "poner", "colocar", "colocan",
        "tiene", "tienen", "esta", "está", "queda", "quedan",
        "hago", "hacen", "voy", "veo", "mando", "paso", "pasa", "pasar",
        "consulta", "aviso", "tema", "resumen", "tratamiento", "trat", "tto",
        "indicacion", "indicación", "indicaciones",
        "dr", "dra", "farmacia", "encargados", "secretaria", "personal",
        "via", "vía", "inicio", "inicia", "continua", "continúa", "termina",
        "hoy", "mañana", "ayer",
        # Medicación / términos frecuentes que se confunden con pacientes
        "paracetamol", "acemuk", "loperamida", "omeprazol", "bactrim", "cipro", "ciprofloxacina",
        "ampicilina", "dexa", "midazolam", "diazepam", "diclofenac", "insulina",
        "hidratacion", "hidratación",
    }

    allowed_connectors = {"de", "del", "dela", "da", "dos", "das"}  # conectores típicos en apellidos

    strong_by_norm: Dict[str, str] = {}
    weak_counts: Dict[str, Dict[str, int]] = {}
    weak_sources: Dict[str, set] = {}

    def consider_candidate(raw_name: str, *, strong: bool, source: str = "") -> None:
        raw_name = raw_name.strip()
        raw_name = re.sub(r"\s{2,}", " ", raw_name)

        if len(raw_name) < 3 or len(raw_name) > 35:
            return

        lowered = raw_name.lower()
        words = lowered.split()
        if not words:
            return

        # Evitar saludos / frases comunes (especialmente como primera palabra)
        if words[0] in stopword_words:
            return

        # Evitar que cualquier palabra "ruidosa" convierta esto en "paciente"
        for w in words:
            if w in allowed_connectors:
                continue
            if w in stopword_words:
                return
            if len(w) <= 2:
                return

        # Evitar tokens con números o signos raros
        if re.search(r"[\d@#]", raw_name):
            return

        norm = normalize_name_for_id(raw_name)
        if strong:
            prev = strong_by_norm.get(norm)
            if prev is None or len(raw_name) > len(prev):
                strong_by_norm[norm] = raw_name
        else:
            by_raw = weak_counts.get(norm)
            if by_raw is None:
                by_raw = {}
                weak_counts[norm] = by_raw
            by_raw[raw_name] = by_raw.get(raw_name, 0) + 1
            if source:
                s = weak_sources.get(norm)
                if s is None:
                    s = set()
                    weak_sources[norm] = s
                s.add(source)

    for msg in messages:
        text = msg.get("text", "").strip()
        if not text:
            continue

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            # Quitar bullets comunes
            line = re.sub(r"^[\*\-\u2022•]+\s*", "", line)

            m1 = name_colon_pattern.match(line)
            if m1:
                consider_candidate(m1.group(1), strong=True)
                continue

            m2 = name_alone_pattern.match(line)
            if m2:
                consider_candidate(m2.group(1), strong=False, source="alone")
                continue

            # Caso: "Paciente Apellido <indicación...>" sin ":"
            m3 = re.match(
                r"^([A-Za-zÁÉÍÓÚÑáéíóúñ]+)(?:\s+([A-Za-zÁÉÍÓÚÑáéíóúñ]+))?\s+(.*)$",
                line,
            )
            if m3:
                w1, w2, rest = m3.group(1), m3.group(2), (m3.group(3) or "").strip()
                if not rest:
                    continue
                lrest = rest.lower()
                if re.search(r"\d", lrest) or any(
                    k in lrest for k in ["mg", "gts", "gotas", "amp", "comp", "hs", "c/", " x "]
                ):
                    if w2:
                        consider_candidate(f"{w1} {w2}", strong=False, source="dose_prefix")
                    else:
                        consider_candidate(w1, strong=False, source="dose_prefix")

    # Armado final:
    # - siempre incluir los "strong" (vienen de "Paciente: ...")
    # - incluir los "weak" si aparecen 2+ veces, o si provienen de "dose_prefix"
    #   (línea que comienza con nombre y sigue con dosis/indicaciones)
    final_by_norm: Dict[str, str] = dict(strong_by_norm)
    for norm, by_raw in weak_counts.items():
        total = sum(by_raw.values())
        sources = weak_sources.get(norm, set())
        if total < 2 and "dose_prefix" not in sources:
            continue
        # Elegir la variante raw más frecuente; desempate por longitud
        best = sorted(by_raw.items(), key=lambda kv: (kv[1], len(kv[0])), reverse=True)[0][0]
        prev = final_by_norm.get(norm)
        if prev is None or len(best) > len(prev):
            final_by_norm[norm] = best

    sorted_names = sorted(final_by_norm.values(), key=lambda s: s.lower())
    patients: List[Dict[str, Any]] = []
    for name in sorted_names:
        normalized = normalize_name_for_id(name)
        # ID determinístico a partir del nombre (estable entre corridas)
        crc = zlib.crc32(normalized.encode("utf-8")) & 0xFFFFFFFF
        patient_id = f"pat_{crc:08d}"
        patient_name = format_patient_display_name(name)
        nickname = ""

        patients.append(
            {
                "patient_id": patient_id,
                "patient": patient_name,
                "nickname": nickname,
                "shiftsPharmacy": [],
            }
        )

    return patients


def normalize_name_for_id(name: str) -> str:
    s = name.strip().lower()
    s = s.replace(",", " ")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", s).strip()
    # Colapsar letras repetidas para tolerar typos (Fonarof/Fonaroff, Paradiso/Paradisso)
    s = re.sub(r"(.)\1+", r"\1", s)
    return s


def load_names_id(path: str) -> Dict[str, Any]:
    """
    Parsea el archivo names-id.txt con formato de tabla Markdown.
    Retorna un dict con los mapas de pacientes y enfermeros.
    """
    nurse_id_map: Dict[str, str] = {}
    nurse_name_map: Dict[str, str] = {}
    patient_id_map: Dict[str, str] = {}
    patient_name_map: Dict[str, str] = {}
    patient_alias_extra: Dict[str, str] = {}
    nurse_alias_extra: Dict[str, str] = {}

    section = None

    if not path or not os.path.exists(path):
        return {
            "nurse_id_map": nurse_id_map,
            "nurse_name_map": nurse_name_map,
            "patient_id_map": patient_id_map,
            "patient_name_map": patient_name_map,
            "patient_alias_extra": patient_alias_extra,
            "nurse_alias_extra": nurse_alias_extra,
        }

    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue

                low = line.lower()
                if low.startswith("## nurses") or low.startswith("## enfermeria") or low.startswith("## enfermería"):
                    section = "nurses"
                    continue
                if low.startswith("## patients") or low.startswith("## pacientes"):
                    section = "patients"
                    continue
                if low.startswith("##"):
                    section = None
                    continue

                if not line.startswith("|"):
                    continue
                cols = [c.strip() for c in line.split("|")]
                cols = [c for c in cols if c]
                if not cols:
                    continue
                if cols[0].startswith("-") or cols[0].lower() in ("nurse_id", "patient_id", "id"):
                    continue

                if section == "nurses" and len(cols) >= 2:
                    nid = cols[0].strip()
                    name = cols[1].strip()
                    if not nid or not name:
                        continue
                    nn = _ni_norm(name)
                    nurse_id_map[nn] = nid
                    nurse_name_map[nn] = name
                    for variant in _ni_name_forms(name):
                        vn = _ni_norm(variant)
                        if vn not in nurse_id_map:
                            nurse_id_map[vn] = nid
                            nurse_name_map[vn] = name

                elif section == "patients" and len(cols) >= 2:
                    pid = cols[0].strip()
                    name = cols[1].strip()
                    aliases_raw = cols[2].strip() if len(cols) >= 3 else ""
                    if not pid or not name:
                        continue
                    nn = _ni_norm(name)
                    patient_id_map[nn] = pid
                    patient_name_map[nn] = name
                    for variant in _ni_name_forms(name):
                        vn = _ni_norm(variant)
                        if vn not in patient_id_map:
                            patient_id_map[vn] = pid
                            patient_name_map[vn] = name
                    if aliases_raw:
                        for alias in aliases_raw.split(","):
                            alias = alias.strip()
                            if not alias:
                                continue
                            an = _ni_norm(alias)
                            if an and an not in patient_alias_extra:
                                patient_alias_extra[an] = pid
                            for variant in _ni_name_forms(alias):
                                vn = _ni_norm(variant)
                                if vn and vn not in patient_alias_extra:
                                    patient_alias_extra[vn] = pid
    except Exception as e:
        print(f"[WARN] Error al cargar names-id: {e}")

    return {
        "nurse_id_map": nurse_id_map,
        "nurse_name_map": nurse_name_map,
        "patient_id_map": patient_id_map,
        "patient_name_map": patient_name_map,
        "patient_alias_extra": patient_alias_extra,
        "nurse_alias_extra": nurse_alias_extra,
    }


def _ni_norm(s: str) -> str:
    """Normalización para matching en names-id: sin tildes, minúsculas, solo alfanum+espacios."""
    s = "".join(
        c for c in unicodedata.normalize("NFD", s or "")
        if unicodedata.category(c) != "Mn"
    ).lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _ni_name_forms(name: str) -> List[str]:
    """Genera variantes Nombre Apellido / Apellido Nombre / Apellido, Nombre."""
    n = (name or "").strip()
    if not n:
        return []
    if "," in n:
        parts = [p.strip() for p in n.split(",", 1)]
        inverted = " ".join(reversed(parts)).strip()
        return [n, inverted]
    toks = n.split()
    if len(toks) >= 2:
        rev = " ".join([toks[-1]] + toks[:-1])
        rev2 = f"{toks[-1]}, {' '.join(toks[:-1])}"
        return [n, rev, rev2]
    return [n]


def resolve_id_from_names(canonical: str, id_map: Dict[str, str], alias_extra: Dict[str, str]) -> Optional[str]:
    """
    Intenta resolver el ID de un nombre usando el mapa del archivo names-id.txt.
    """
    if not canonical:
        return None
    nn = _ni_norm(canonical)
    if nn in id_map:
        return id_map[nn]
    if nn in alias_extra:
        return alias_extra[nn]
    best_id = None
    best_sc = 0.0
    for key, kid in id_map.items():
        sc = SequenceMatcher(None, nn, key).ratio()
        if sc > best_sc:
            best_sc = sc
            best_id = kid
    if best_sc >= 0.82:
        return best_id
    return None


def resolve_name_from_id(pid: str, id_to_name: Dict[str, str]) -> str:
    """Devuelve el nombre canonical dado un ID."""
    return id_to_name.get(pid, "")


def format_patient_display_name(name: str) -> str:
    """
    Intento de formateo: si viene "Apellido Nombre", lo devuelve "Apellido, Nombre".
    Si viene 1 sola palabra, la deja igual (capitalizada).
    """
    clean = re.sub(r"\s{2,}", " ", name.strip())
    if "," in clean:
        left, right = [x.strip() for x in clean.split(",", 1)]
        left = left[:1].upper() + left[1:].lower() if left else left
        right = " ".join(w[:1].upper() + w[1:].lower() for w in right.split())
        return f"{left}, {right}".strip().strip(",")

    parts = clean.split()
    # Sin coma: no asumimos orden (puede venir "Pérez Emma" o "Emma Pérez").
    # Capitalizamos y devolvemos tal cual (sin forzar coma).
    return " ".join(p[:1].upper() + p[1:].lower() for p in parts)


def build_patient_index(patients: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    A partir de la lista de pacientes del JSON, arma un índice con posibles
    tokens de búsqueda que aparecen en el chat (apellido, apellido + nombre,
    nickname, etc.).
    """
    index: List[Dict[str, Any]] = []

    for p in patients:
        patient_name = p.get("patient", "")
        nickname = p.get("nickname", "") or ""
        patient_id = p.get("patient_id")

        tokens: List[str] = []

        if "," in patient_name:
            surname = ""
            given_names = ""
            parts = [x.strip() for x in patient_name.split(",", 1)]
            surname = parts[0]
            given_names = parts[1] if len(parts) > 1 else ""

            if surname:
                tokens.append(surname.lower())
            if surname and given_names:
                # tokens con coma y sin coma para enganchar "Abud, Julio" y "Abud julio"
                tokens.append(f"{surname.lower()}, {given_names.lower()}")
                tokens.append(f"{surname.lower()} {given_names.lower()}")
                tokens.append(f"{surname.lower()} {given_names.split()[0].lower()}")
        else:
            # Sin coma: generar tokens robustos para 1-3 palabras
            words = [w for w in patient_name.strip().split() if w]
            if words:
                full = " ".join(words).lower()
                tokens.append(full)
                # cada palabra suelta
                tokens.extend(w.lower() for w in words)
                # último token (muchas veces es apellido)
                if len(words) >= 2:
                    tokens.append(words[-1].lower())
                # reverso para 2 palabras (cubre "Emma Perez" vs "Perez Emma")
                if len(words) == 2:
                    tokens.append(f"{words[1].lower()} {words[0].lower()}")

        if nickname:
            tokens.append(nickname.lower())

        def normalize_token(t: str) -> str:
            t2 = t.strip().lower()
            t2 = unicodedata.normalize("NFKD", t2)
            t2 = "".join(ch for ch in t2 if not unicodedata.combining(ch))
            # quitar puntuación simple para matchear variantes en chat
            t2 = re.sub(r"[.,;]", "", t2)
            # colapsar repetidos (Fonarof/Fonaroff, Paradiso/Paradisso)
            t2 = re.sub(r"(.)\1+", r"\1", t2)
            t2 = re.sub(r"\s+", " ", t2).strip()
            return t2

        expanded: List[str] = []
        for t in tokens:
            if not t:
                continue
            expanded.append(t)
            nt = normalize_token(t)
            if nt and nt != t:
                expanded.append(nt)

        # Normalizar, evitar duplicados y ordenar por longitud (primero los más largos)
        seen = set()
        dedup_tokens_raw = []
        for t in expanded:
            t = t.strip()
            if t and t not in seen:
                dedup_tokens_raw.append(t)
                seen.add(t)

        dedup_tokens = sorted(dedup_tokens_raw, key=len, reverse=True)

        index.append(
            {
                "patient_id": patient_id,
                "patient": patient_name,
                "nickname": nickname,
                "tokens": dedup_tokens,
            }
        )

    return index


def find_patient_for_message(
    text: str, patient_index: List[Dict[str, Any]]
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Intenta asignar un mensaje a un paciente buscando si el texto comienza
    con alguno de los tokens (apellido, nickname, etc.).
    Devuelve (paciente, token_matched) o (None, None).
    """
    txt_lower = text.lstrip().lower()

    for p in patient_index:
        for token in p["tokens"]:
            if not token:
                continue
            if txt_lower.startswith(token):
                next_char_index = len(token)
                if next_char_index >= len(txt_lower) or txt_lower[next_char_index] in {
                    " ",
                    ":",
                    ",",
                    ".",
                    "-",
                    ";",
                    "'",
                    '"',
                }:
                    return p, token

    return None, None


def remove_patient_prefix(text: str, token: str) -> str:
    """
    Elimina del comienzo del texto la mención al paciente (el token)
    junto con separadores como ':' o espacios.
    """
    pattern = re.compile(rf"^(?i:{re.escape(token)})[\s:,-]*")
    new_text = pattern.sub("", text.lstrip())
    return new_text.strip()


def build_pharmacy_summary(
    messages: List[Dict[str, Any]],
    patient_index: List[Dict[str, Any]],
    base_patients: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    A partir de los mensajes del chat y el índice de pacientes, construye
    la estructura final tipo resultado_extraccion_pharmacy.json.

    - Toma los name-id del JSON existente (base_patients).
    - Respeta exactamente el formato del proyecto:
      patient_id, patient, nickname, shiftsPharmacy[date, responsableNurse, responsable_id, resumen].
    - Recalcula shiftsPharmacy solo en base al parser (sin IA).
    """
    # Mapa patient_id -> lista de shifts
    shifts_by_patient: Dict[str, List[Dict[str, Any]]] = {
        p["patient_id"]: [] for p in base_patients if p.get("patient_id")
    }

    for msg in messages:
        text = msg["text"]
        sender = msg["sender"]
        dt: datetime = msg["datetime"]

        patient_info, token = find_patient_for_message(text, patient_index)
        if not patient_info or not token:
            continue

        resumen = remove_patient_prefix(text, token)
        if not resumen:
            continue

        patient_id = patient_info.get("patient_id")
        if not patient_id:
            continue

        iso_date = dt.strftime("%Y-%m-%dT%H:%M:00")
        shift = {
            "date": iso_date,
            "responsableNurse": sender,
            "responsable_id": "unknown_enf",
            "resumen": resumen,
        }
        shifts_by_patient.setdefault(patient_id, []).append(shift)

    # Ordenar shifts por fecha
    for pid, shifts in shifts_by_patient.items():
        shifts.sort(key=lambda s: s["date"])

    # Construir lista final de pacientes con sus shiftsPharmacy
    result: List[Dict[str, Any]] = []
    for p in base_patients:
        pid = p.get("patient_id")
        if not pid:
            continue
        result.append(
            {
                "patient_id": pid,
                "patient": p.get("patient"),
                "nickname": p.get("nickname", ""),
                "shiftsPharmacy": shifts_by_patient.get(pid, []),
            }
        )

    return result


def segment_text_by_patient(
    text: str,
    patient_index: List[Dict[str, Any]],
    *,
    on_new_patient=None,
    known_single_tokens: Optional[set] = None,
) -> List[Tuple[Dict[str, Any], str]]:
    """
    Divide el texto de un mensaje en segmentos por paciente.

    Soporta:
    - Mensajes multi-línea donde aparece el paciente como encabezado de línea
      y las líneas siguientes pertenecen a ese paciente hasta que aparezca otro.
    - Líneas con bullets "* Paciente ..." / "- Paciente ..." / "• Paciente ..."
    """
    segments: Dict[str, Dict[str, Any]] = {}
    current_patient: Optional[Dict[str, Any]] = None
    shared_resumen: Optional[str] = None  # para casos tipo "LACTULON UCP" + lista de pacientes
    current_drug_label: Optional[str] = None  # sub-header de medicamento ("Loperamida", "Diclofenac")

    name_colon_pattern = re.compile(
        r"^([A-Za-zÁÉÍÓÚÑáéíóúñ]+(?:\s+[A-Za-zÁÉÍÓÚÑáéíóúñ]+){0,2})\s*:\s+(.+)$"
    )
    name_alone_pattern = re.compile(
        r"^([A-Za-zÁÉÍÓÚÑáéíóúñ]+(?:\s+[A-Za-zÁÉÍÓÚÑáéíóúñ]+){0,2})$"
    )

    lines = text.splitlines() if "\n" in text else [text]

    # --- PRE-ANÁLISIS: detectar si el mensaje es una "lista de pacientes"
    # Patrón: primera(s) línea(s) son indicación + luego líneas que son SOLO nombres de pacientes.
    # Ejemplo:
    #   "Buenas todos en caso de SOS halopi"   <- indicación compartida
    #   "Piriz"                                 <- solo nombre
    #   "Zatti"                                 <- solo nombre
    # En este caso, la indicación compartida puede NO empezar en la línea inmediatamente
    # anterior al primer paciente — puede ser una frase libre que no matchea paciente.
    non_empty_lines = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("<Multimedia omitido>"):
            continue
        if stripped.startswith("Se eliminó este mensaje."):
            continue
        non_empty_lines.append(re.sub(r"^[\*\-\u2022•]+\s*", "", stripped))

    if non_empty_lines:
        # Detectar si la primera línea NO es un paciente y la mayoría de las siguientes SÍ lo son
        first_line = non_empty_lines[0]
        first_is_patient, _ = find_patient_for_message(first_line, patient_index)
        if not first_is_patient and len(non_empty_lines) >= 2:
            # Contar cuántas de las líneas restantes son SOLO nombres de pacientes
            patient_only_count = 0
            for candidate in non_empty_lines[1:]:
                ci, ct = find_patient_for_message(candidate, patient_index)
                if ci and ct:
                    remainder_c = remove_patient_prefix(candidate, ct)
                    if not remainder_c:  # línea es solo nombre, sin indicación propia
                        patient_only_count += 1
            # Si al menos la mitad de las líneas restantes son solo nombres → shared_resumen
            remaining_count = len(non_empty_lines) - 1
            if remaining_count > 0 and patient_only_count >= max(1, remaining_count // 2):
                shared_resumen = first_line

    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue

        # Ignorar ruido típico de export de WhatsApp
        if line.startswith("<Multimedia omitido>"):
            continue
        if line.startswith("Se eliminó este mensaje."):
            continue

        # Sacar bullets
        line_no_bullet = re.sub(r"^[\*\-\u2022•]+\s*", "", line)

        # Detectar patrón tipo:
        #   LACTULON UCP
        #   •ABUD JULIO
        #   •FERREYRA NILDA
        # Es decir, una línea de texto genérica seguida por viñetas que son solo nombres
        # de pacientes. En ese caso, usamos la línea actual como resumen compartido.
        # (Esta lógica complementa el pre-análisis de arriba para casos con bullets)
        if shared_resumen is None:
            tmp_info, tmp_token = find_patient_for_message(line_no_bullet, patient_index)
            if not tmp_info and line_no_bullet:
                # mirar la próxima línea no vacía
                next_line_clean = ""
                for j in range(i + 1, len(lines)):
                    nl = lines[j].strip()
                    if not nl:
                        continue
                    next_line_clean = re.sub(r"^[\*\-\u2022•]+\s*", "", nl)
                    break

                if next_line_clean:
                    ni, nt = find_patient_for_message(next_line_clean, patient_index)
                    if ni and nt:
                        remainder_next = remove_patient_prefix(next_line_clean, nt)
                        # si la siguiente línea es solo el nombre del paciente (sin resto),
                        # asumimos que la línea actual es la indicación compartida
                        if not remainder_next:
                            shared_resumen = line_no_bullet
                            current_drug_label = None  # shared_resumen tiene prioridad

        # Detectar sub-header de medicamento:
        # Línea corta (1-2 palabras), sin números, sin viñeta, no matchea ningún paciente,
        # y la siguiente línea sí es un paciente con dosis.
        # Ej: "Loperamida", "Diclofenac", "Hierro"
        if not shared_resumen:
            tmp2_info, _ = find_patient_for_message(line_no_bullet, patient_index)
            if not tmp2_info:
                drug_words = line_no_bullet.split()
                if 1 <= len(drug_words) <= 3 and not re.search(r"[\d@]{1}", line_no_bullet):
                    # verificar que la próxima línea sea paciente con resto de dosis
                    next_l = ""
                    for j in range(i + 1, len(lines)):
                        nl2 = lines[j].strip()
                        if nl2:
                            next_l = re.sub(r"^[\*\-\u2022•]+\s*", "", nl2)
                            break
                    if next_l:
                        ni2, nt2 = find_patient_for_message(next_l, patient_index)
                        if ni2 and nt2:
                            remainder_next2 = remove_patient_prefix(next_l, nt2)
                            # solo si el paciente siguiente tiene dosis (resto no vacío)
                            if remainder_next2 and looks_like_dosage_or_instruction(remainder_next2):
                                current_drug_label = line_no_bullet.strip()

        patient_info, token = find_patient_for_message(line_no_bullet, patient_index)
        if patient_info and token:
            current_patient = patient_info
            remainder = remove_patient_prefix(line_no_bullet, token)
            pid = patient_info.get("patient_id")
            if not pid:
                continue

            entry = segments.get(pid)
            if not entry:
                entry = {"patient": patient_info, "lines": []}
                segments[pid] = entry

            # Si la línea es una viñeta en un bloque con resumen compartido y el "resto"
            # después del apellido es solo un nombre (ej. "RICARDO" en "ALCAIN RICARDO"),
            # lo tratamos como parte del nombre y NO como resumen.
            if shared_resumen and remainder and name_alone_pattern.match(remainder):
                remainder = ""

            # Si la línea es solo nombre y tenemos un resumen compartido, usamos ese resumen.
            if not remainder and shared_resumen:
                entry["lines"].append(shared_resumen)
            elif remainder:
                # Si hay un sub-header de medicamento activo (ej: "Loperamida"),
                # prefijar el resumen con ese contexto para no perder la droga.
                if current_drug_label and not shared_resumen:
                    entry["lines"].append(f"{current_drug_label}: {remainder}")
                else:
                    entry["lines"].append(remainder)
            continue

        # Si no matchea ningún paciente conocido, intentamos inferir uno nuevo (on-the-fly)
        if on_new_patient is not None:
            m1 = name_colon_pattern.match(line_no_bullet)
            if m1 and is_candidate_patient_label(m1.group(1)):
                inferred_label = m1.group(1).strip()
                inferred_remainder = m1.group(2).strip()
                new_patient = on_new_patient(inferred_label)
                if new_patient:
                    current_patient = new_patient
                    pid = new_patient.get("patient_id")
                    if pid:
                        entry = segments.get(pid)
                        if not entry:
                            entry = {"patient": new_patient, "lines": []}
                            segments[pid] = entry
                        if inferred_remainder:
                            entry["lines"].append(inferred_remainder)
                    continue

            m2 = name_alone_pattern.match(line_no_bullet)
            if m2 and is_candidate_patient_label(m2.group(1)):
                # Sólo si la próxima línea "parece" ser una indicación/dosis
                next_line = ""
                for j in range(i + 1, len(lines)):
                    nl = lines[j].strip()
                    if nl:
                        next_line = nl
                        break
                if next_line and looks_like_dosage_or_instruction(next_line):
                    inferred_label = m2.group(1).strip()
                    new_patient = on_new_patient(inferred_label)
                    if new_patient:
                        current_patient = new_patient
                        continue

            # Caso: "Paciente Apellido <indicación...>" sin ":" (muy común)
            # Ej: "Abud julio traer barex..." / "Daniel Rodríguez loperamida..."
            m3 = re.match(r"^([A-Za-zÁÉÍÓÚÑáéíóúñ]+)(?:\s+([A-Za-zÁÉÍÓÚÑáéíóúñ]+))?\s+(.*)$", line_no_bullet)
            if m3:
                w1, w2, rest = m3.group(1), m3.group(2), m3.group(3)
                rest = (rest or "").strip()
                if rest and looks_like_dosage_or_instruction(rest):
                    # Para evitar falsos positivos ("Ella tiene...", "Necesitamos...", etc),
                    # sólo habilitamos este modo si el primer token ya es conocido como paciente.
                    if known_single_tokens is not None:
                        w1n = normalize_name_for_id(w1)
                        if (w1.lower() not in known_single_tokens) and (w1n not in known_single_tokens):
                            # no inferimos pacientes nuevos desde frases comunes
                            pass
                        else:
                            # intentamos 2 palabras primero
                            if w2:
                                cand2 = f"{w1} {w2}"
                                if is_candidate_patient_label(cand2):
                                    new_patient = on_new_patient(cand2)
                                    if new_patient:
                                        current_patient = new_patient
                                        pid = new_patient.get("patient_id")
                                        if pid:
                                            entry = segments.get(pid)
                                            if not entry:
                                                entry = {"patient": new_patient, "lines": []}
                                                segments[pid] = entry
                                            entry["lines"].append(rest)
                                        continue

                            cand1 = w1
                            if is_candidate_patient_label(cand1):
                                new_patient = on_new_patient(cand1)
                                if new_patient:
                                    current_patient = new_patient
                                    pid = new_patient.get("patient_id")
                                    if pid:
                                        entry = segments.get(pid)
                                        if not entry:
                                            entry = {"patient": new_patient, "lines": []}
                                            segments[pid] = entry
                                        entry["lines"].append(rest)
                                    continue

        # Si no hay match nuevo, agregamos al paciente actual (si existe),
        # salvo que estemos en un bloque de viñetas con resumen compartido
        # (para no meter los nombres de otros pacientes dentro del resumen).
        if current_patient and current_patient.get("patient_id") in segments:
            # Caso típico de lista:
            #   LACTULON UCP
            #   •ABUD JULIO
            #   •FERREYRA NILDA
            # En ese contexto, las líneas que son SOLO nombres se omiten aquí.
            if shared_resumen and name_alone_pattern.match(line_no_bullet):
                continue
            segments[current_patient["patient_id"]]["lines"].append(line_no_bullet)

    out: List[Tuple[Dict[str, Any], str]] = []
    for entry in segments.values():
        resumen = "\n".join(entry["lines"]).strip()
        if not resumen:
            continue
        # Limpiar artefactos de WhatsApp (edición, emojis solos, etc.) antes de evaluar
        resumen = clean_resumen(resumen)
        if not resumen:
            continue
        # Filtrar segmentos donde el resumen es solo el nombre del paciente (sin indicación real)
        patient_name_raw = entry["patient"].get("patient", "") or ""
        patient_tokens = set()
        for part in patient_name_raw.replace(",", " ").split():
            patient_tokens.add(part.lower())
            patient_tokens.add(normalize_name_for_id(part))
        resumen_norm = normalize_name_for_id(resumen)
        if resumen_norm in patient_tokens or resumen.lower() in {t.lower() for t in patient_tokens}:
            # el resumen ES solo el nombre del paciente → descartamos
            continue
        # Filtrar resumenes sin valor clínico real (ej: "ayer", "el sábado", "ok", etc.)
        if not is_meaningful_resumen(resumen):
            continue
        out.append((entry["patient"], resumen))

    return out


def clean_resumen(text: str) -> str:
    """
    Limpia artefactos de WhatsApp y ruido del resumen:
    - Elimina marcadores de edición/eliminación de WhatsApp.
    - Elimina secuencias de emojis/flechas al inicio y final.
    - Normaliza espacios.
    """
    # Eliminar marcadores de WhatsApp
    text = re.sub(r"<Se editó este mensaje\.>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<Se eliminó este mensaje\.>", "", text, flags=re.IGNORECASE)
    # Eliminar secuencias de emojis/flechas/símbolos especiales al inicio y final
    # (cualquier carácter que no sea letra, número, puntuación española básica)
    text = re.sub(r"^[^\w\dáéíóúüà-ÿ\-.,;:!?()\[\]]+", "", text.strip())
    text = re.sub(r"[^\w\dáéíóúüà-ÿ\-.,;:!?()\[\]]+$", "", text.strip())
    # Normalizar espacios y saltos de línea internos
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def is_meaningful_resumen(resumen: str) -> bool:
    """
    Devuelve False si el resumen no tiene valor clínico real:
    - Una sola palabra que es stopword temporal o social (ayer, mañana, ok, sí, no, etc.)
    - Frases muy cortas (1-2 palabras) sin ningún término clínico reconocible.
    - Frases específicas conocidas que no aportan información médica.
    """
    EMPTY_WORDS = {
        "ayer", "hoy", "mañana", "manana",
        "lunes", "martes", "miercoles", "miércoles", "jueves",
        "viernes", "sabado", "sábado", "domingo",
        "ok", "si", "sí", "no", "gracias", "perfecto", "listo",
        "ahora", "luego", "después", "despues", "antes",
        "tarde", "noche", "dia", "día",
        "proceso", "nueva", "nuevo", "indicacion", "indicación",
        "bien", "confirmado",
    }
    # Frases de 2-3 palabras que tampoco tienen valor clínico
    EMPTY_PHRASES = {
        "en proceso",
        "segun necesidad", "según necesidad",
        "nueva indicacion", "nueva indicación",
        "sin cambios",
        "igual que antes",
        "se mantiene",
        "a evaluar",
        "por confirmar",
    }
    # Términos que garantizan valor clínico aunque la frase sea corta
    CLINICAL_KEYWORDS = [
        "mg", "gts", "gotas", "amp", "ampolla", "comp", "comprim",
        "hs", "c/", "x ", "día", "dias", "días", "via", "vía",
        "tto", "trat", "tratamiento",
        "suspender", "suspende", "suspend", "iniciar", "inicia",
        "continua", "continúa", "continuar",
        "susp", "alt", "iv", "im", "sc", "sng", "php", "sos",
        "ITU", "itu", "infeccion", "infección",
    ]

    stripped = resumen.strip()
    if not stripped:
        return False

    lower = stripped.lower()
    # Eliminar puntuación marginal para comparar frases
    lower_clean = re.sub(r"[.,;!?]", "", lower).strip()
    words = lower_clean.split()

    # Verificar primero contra frases vacías conocidas
    if lower_clean in EMPTY_PHRASES:
        return False

    # Si tiene números → muy probable que sea una dosis real
    if re.search(r"\d", lower):
        return True

    # Si contiene algún keyword clínico → válido
    for kw in CLINICAL_KEYWORDS:
        if kw.lower() in lower:
            return True

    # Si es una sola palabra y está en el conjunto de palabras vacías → descartar
    if len(words) == 1 and words[0] in EMPTY_WORDS:
        return False

    # Si es 2 palabras o menos y TODAS son palabras vacías → descartar
    if len(words) <= 2 and all(w in EMPTY_WORDS for w in words):
        return False

    # En cualquier otro caso (frase con 3+ palabras o con contenido desconocido)
    # la dejamos pasar para no perder información real
    return True


def looks_like_dosage_or_instruction(line: str) -> bool:
    l = line.lower()
    if re.search(r"\d", l):
        return True
    keywords = ["mg", "gts", "gotas", "amp", "ampolla", "comp", "comprim", "hs", "c/", "x ", "día", "dias", "días"]
    return any(k in l for k in keywords)


def is_candidate_patient_label(raw_name: str) -> bool:
    raw_name = raw_name.strip()
    if len(raw_name) < 3 or len(raw_name) > 35:
        return False
    if re.search(r"[\d@#]", raw_name):
        return False

    lowered = raw_name.lower()
    words = lowered.split()
    if not words:
        return False

    stopword_words = {
        "buen", "buenas", "dia", "día", "dias", "días", "tarde", "tardes", "noche", "noches",
        "hola", "gracias", "ok", "perfecto", "listo", "si", "sí", "no",
        "para", "por", "porque", "y", "el", "la", "los", "las", "un", "una",
        "necesito", "necesitamos", "pedir", "pide", "piden", "avisar", "avisen", "aviso",
        "preparar", "preparen", "poner", "colocar", "colocan", "iniciar", "inicia",
        "continua", "continúa", "termina", "traer", "traigan",
        "tiene", "tienen", "esta", "está", "queda", "quedan",
        "hago", "hacen", "voy", "veo", "mando", "paso", "pasa", "pasar",
        "consulta", "aviso", "tema", "resumen", "tratamiento", "trat", "tto",
        "indicacion", "indicación", "indicaciones",
        "dr", "dra", "farmacia", "encargados", "secretaria", "personal",
        "via", "vía", "inicio", "inicia", "continua", "continúa", "termina",
        "hoy", "mañana", "ayer",
        # Medicación / términos frecuentes que se confunden con pacientes
        "paracetamol", "acemuk", "loperamida", "omeprazol", "bactrim", "cipro", "ciprofloxacina",
        "ampicilina", "dexa", "midazolam", "diazepam", "diclofenac", "insulina",
        "hidratacion", "hidratación",
    }
    allowed_connectors = {"de", "del", "da", "dos", "das"}

    if words[0] in stopword_words:
        return False

    for w in words:
        if w in allowed_connectors:
            continue
        if w in stopword_words:
            return False
        if len(w) <= 2:
            return False

    return True


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Parser de indicaciones farmacéuticas para RGP (sin IA)."
    )
    ap.add_argument("input_txt", help="Ruta al .txt exportado de WhatsApp.")
    ap.add_argument("output_json", help="Nombre/ruta del JSON de salida.")
    ap.add_argument(
        "--names_id",
        default=None,
        help="Ruta al archivo names-id.txt con IDs reales.",
    )
    args = ap.parse_args()

    print("--- Parser de indicaciones farmacéuticas (sin IA) ---")

    chat_path = args.input_txt
    if not os.path.exists(chat_path):
        print(f"Error: no se encontró el archivo de chat '{chat_path}'")
        return

    print(f"Leyendo chat desde: {chat_path}")
    messages = parse_whatsapp_chat(chat_path)
    print(f"Total de mensajes parseados: {len(messages)}")

    # Carga de names-id
    names_id_path = args.names_id or NAMES_ID_FILENAME
    mapping = load_names_id(names_id_path)
    
    nurses_id_map = mapping["nurse_id_map"]
    patient_id_map = mapping["patient_id_map"]
    patient_alias_extra = mapping["patient_alias_extra"]
    patient_name_canonical = mapping["patient_name_map"]
    nurse_alias_extra = mapping["nurse_alias_extra"]

    patients: List[Dict[str, Any]] = []

    # Si hay mapeo de pacientes, los usamos como base
    if patient_id_map:
        seen_pids = set()
        # pacient_id_map tiene norm(nombre) -> patient_id
        for p_norm, pid in patient_id_map.items():
            if pid in seen_pids:
                continue
            name = patient_name_canonical.get(p_norm, p_norm)
            patients.append({
                "patient_id": pid,
                "patient": name,
                "nickname": "",
                "shiftsPharmacy": [],
            })
            seen_pids.add(pid)
        print(f"Pacientes cargados desde names-id: {len(patients)}")
    else:
        print("Aviso: no se encontró mapeo de pacientes, se inferirán del chat.")
        patients = infer_patients_from_chat(messages)

    if not patients:
        print("No se pudieron determinar pacientes.")
        return

    patients_by_norm: Dict[str, Dict[str, Any]] = {}
    for p in patients:
        n = normalize_name_for_id(p.get("patient", ""))
        if n and n not in patients_by_norm:
            patients_by_norm[n] = p

    patient_index = build_patient_index(list(patients_by_norm.values()))

    known_single_tokens: set = set()
    for entry in patient_index:
        for t in entry.get("tokens", []):
            if " " in t: continue
            if len(t) < 3: continue
            known_single_tokens.add(t.lower())
            known_single_tokens.add(normalize_name_for_id(t))

    def on_new_patient(label: str) -> Optional[Dict[str, Any]]:
        display = format_patient_display_name(label)
        n = normalize_name_for_id(display)
        
        # Primero buscar si ya existe en lo que tenemos cargado
        existing = patients_by_norm.get(n)
        if existing: return existing

        # Si hay names-id, intentar resolver via coincidencia difusa/alias
        if patient_id_map:
            resolved_id = resolve_id_from_names(display, patient_id_map, patient_alias_extra)
            if resolved_id:
                # Buscar el objeto paciente ya cargado con ese ID
                for p_obj in patients_by_norm.values():
                    if p_obj.get("patient_id") == resolved_id:
                        return p_obj

        # Fallback: creación dinámica si no hay names-id o no se encontró
        if not patient_id_map:
            crc = zlib.crc32(n.encode("utf-8")) & 0xFFFFFFFF
            patient_id = f"pat_{crc:08d}"
            new_p = {
                "patient_id": patient_id,
                "patient": display,
                "nickname": "",
                "shiftsPharmacy": [],
            }
            patients_by_norm[n] = new_p
            patient_index.extend(build_patient_index([new_p]))
            return new_p
        
        return None

    shifts_by_patient: Dict[str, List[Dict[str, Any]]] = {
        p["patient_id"]: [] for p in patients_by_norm.values() if p.get("patient_id")
    }

    for msg in messages:
        sender = msg["sender"]
        dt: datetime = msg["datetime"]
        iso_date = dt.strftime("%Y-%m-%dT%H:%M:00")

        # Resolver responsable_id usando la misma lógica de enfermería
        responsable_id = resolve_id_from_names(sender, nurses_id_map, nurse_alias_extra) or "unknown_enf"

        for patient_info, resumen in segment_text_by_patient(
            msg["text"],
            patient_index,
            on_new_patient=on_new_patient,
            known_single_tokens=known_single_tokens,
        ):
            pid = patient_info.get("patient_id")
            if not pid: continue
            
            shifts_by_patient.setdefault(pid, []).append({
                "date": iso_date,
                "responsableNurse": sender,
                "responsable_id": responsable_id,
                "resumen": resumen,
            })

    for pid, shifts in shifts_by_patient.items():
        shifts.sort(key=lambda s: s["date"])

    result: List[Dict[str, Any]] = []
    # Ordenar por nombre
    all_patients = sorted(patients_by_norm.values(), key=lambda x: (x.get("patient") or "").lower())
    for p in all_patients:
        pid = p.get("patient_id")
        if not pid: continue
        result.append({
            "patient_id": pid,
            "patient": p.get("patient"),
            "nickname": p.get("nickname", ""),
            "shiftsPharmacy": shifts_by_patient.get(pid, []),
        })

    output_filename = args.output_json
    with open(output_filename, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    total_indications = sum(len(p.get("shiftsPharmacy", [])) for p in result)
    
    date_start = messages[0]["datetime"].isoformat() if messages else None
    date_end = messages[-1]["datetime"].isoformat() if messages else None

    # Imprimir estadísticas para captura externa
    import json as json_stats
    stats_out = {
        "patients": len(result),
        "total_indications": total_indications,
        "date_range": [date_start, date_end],
        "total_messages": len(messages)
    }
    print(f"JSON_STATS:{json_stats.dumps(stats_out)}")

    print("\n--- Proceso finalizado ---")
    print(f"Pacientes procesados: {len(result)}")
    print(f"Archivo generado: {output_filename}")


if __name__ == "__main__":
    main()


