import argparse
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

# ----------------------------
# Carga de names-id.txt
# ----------------------------

def load_names_id(path: str) -> Dict[str, Any]:
    """
    Parsea el archivo names-id.txt con formato de tabla Markdown.
    Retorna un dict con:
      - nurse_id_map:    {norm(nombre_canonical) -> nurse_id}
      - nurse_name_map:  {norm(nombre_canonical) -> nombre_canonical}
      - patient_id_map:  {norm(nombre_canonical) -> patient_id}
      - patient_name_map:{norm(nombre_canonical) -> nombre_canonical}
      - patient_alias_map:{norm(alias) -> patient_id}  (alias -> id directo)
      - nurse_alias_map: {norm(alias_raw) -> nurse_id}
    """
    nurse_id_map: Dict[str, str] = {}
    nurse_name_map: Dict[str, str] = {}
    patient_id_map: Dict[str, str] = {}
    patient_name_map: Dict[str, str] = {}
    patient_alias_extra: Dict[str, str] = {}  # norm(alias) -> patient_id
    nurse_alias_extra: Dict[str, str] = {}    # norm(alias) -> nurse_id

    section = None  # "nurses" | "patients"

    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue

                # Detectar sección
                low = line.lower()
                if low.startswith("## nurses") or low.startswith("## enfermería") or low.startswith("## enfermeria"):
                    section = "nurses"
                    continue
                if low.startswith("## patients") or low.startswith("## pacientes"):
                    section = "patients"
                    continue
                if low.startswith("##"):
                    section = None
                    continue

                # Skip separadores y cabeceras de tabla
                if not line.startswith("|"):
                    continue
                cols = [c.strip() for c in line.split("|")]
                cols = [c for c in cols if c]  # eliminar vacíos de bordes
                if not cols:
                    continue
                # Skip header row y separator row
                if cols[0].startswith("-") or cols[0].lower() in (
                    "nurse_id", "patient_id", "id"
                ):
                    continue

                if section == "nurses" and len(cols) >= 2:
                    nid = cols[0].strip()
                    name = cols[1].strip()
                    if not nid or not name:
                        continue
                    nn = _ni_norm(name)
                    nurse_id_map[nn] = nid
                    nurse_name_map[nn] = name
                    # también registrar variantes (Apellido, Nombre -> Nombre Apellido)
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
                    # variantes del nombre canonical
                    for variant in _ni_name_forms(name):
                        vn = _ni_norm(variant)
                        if vn not in patient_id_map:
                            patient_id_map[vn] = pid
                            patient_name_map[vn] = name
                    # aliases explícitos
                    if aliases_raw:
                        for alias in aliases_raw.split(","):
                            alias = alias.strip()
                            if not alias:
                                continue
                            an = _ni_norm(alias)
                            if an and an not in patient_alias_extra:
                                patient_alias_extra[an] = pid
                            # también variantes del alias
                            for variant in _ni_name_forms(alias):
                                vn = _ni_norm(variant)
                                if vn and vn not in patient_alias_extra:
                                    patient_alias_extra[vn] = pid

    except FileNotFoundError:
        print(f"[WARN] names-id.txt no encontrado en {path!r}. Se usarán IDs generados.")

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
    # Si tiene coma  ("Aranda, Brisa") -> invertir en "Brisa Aranda"
    if "," in n:
        parts = [p.strip() for p in n.split(",", 1)]
        inverted = " ".join(reversed(parts)).strip()
        return [n, inverted]
    # Si es "Nombre Apellido" -> generar "Apellido Nombre" y "Apellido, Nombre"
    toks = n.split()
    if len(toks) >= 2:
        rev = " ".join([toks[-1]] + toks[:-1])
        rev2 = f"{toks[-1]}, {' '.join(toks[:-1])}"
        return [n, rev, rev2]
    return [n]


def resolve_id_from_names(canonical: str, id_map: Dict[str, str], alias_extra: Dict[str, str]) -> Optional[str]:
    """
    Intenta resolver el ID de un nombre canonicalizado usando el mapa del archivo.
    Primero exacto (normalized), luego alias extras, luego fuzzy suave.
    """
    if not canonical:
        return None
    nn = _ni_norm(canonical)
    if nn in id_map:
        return id_map[nn]
    if nn in alias_extra:
        return alias_extra[nn]
    # fuzzy: buscar el más parecido
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

# ----------------------------
# Normalización
# ----------------------------

def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn")

def norm(s: str) -> str:
    s = strip_accents(s).lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def name_tokens(s: str) -> List[str]:
    return [t for t in norm(s).split() if t]

def jaccard(a: List[str], b: List[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 0.0
    return len(sa & sb) / max(1, len(sa | sb))

def name_similarity(a: str, b: str) -> float:
    an = norm(a); bn = norm(b)
    if not an or not bn:
        return 0.0

    base = SequenceMatcher(None, an, bn).ratio()

    at = name_tokens(a); bt = name_tokens(b)
    a_rev = " ".join(reversed(at))
    b_rev = " ".join(reversed(bt))

    rev1 = SequenceMatcher(None, a_rev, bn).ratio() if a_rev else 0.0
    rev2 = SequenceMatcher(None, an, b_rev).ratio() if b_rev else 0.0

    jac = jaccard(at, bt)

    boost = 0.0
    if at and bt and at[-1] == bt[-1]:
        boost += 0.04

    return max(base, rev1, rev2, 0.85 * jac + 0.15 * base) + boost

def minus_2_months(dt: datetime) -> datetime:
    try:
        from dateutil.relativedelta import relativedelta  # type: ignore
        return dt - relativedelta(months=2)
    except Exception:
        return dt - timedelta(days=60)

# ----------------------------
# Parseo WhatsApp
# ----------------------------

@dataclass
class Message:
    dt: datetime
    sender: str
    text: str

PATTERNS = [
    re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})\s+(\d{1,2}):(\d{2})\s+-\s+(.*)$"),
    re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2,4}),\s+(\d{1,2}):(\d{2})\s+-\s+(.*)$"),
    re.compile(r"^\[(\d{1,2})/(\d{1,2})/(\d{2,4}),\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\]\s+(.*)$"),
]

def parse_dt(d: str, m: str, y: str, hh: str, mm: str) -> datetime:
    dd = int(d); mo = int(m); yy = int(y); h = int(hh); mi = int(mm)
    if yy < 100:
        yy += 2000
    return datetime(yy, mo, dd, h, mi)

def split_sender_text(rest: str) -> Tuple[str, str]:
    if ": " in rest:
        a, b = rest.split(": ", 1)
        return a.strip(), b.strip()
    return "", rest.strip()

def parse_export(path: str) -> List[Message]:
    msgs: List[Message] = []
    cur: Optional[Message] = None

    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")

            m = None
            for p in PATTERNS:
                m = p.match(line)
                if m:
                    break

            if m:
                g = m.groups()
                if len(g) == 6:
                    d, mo, y, hh, mm, rest = g
                    dt = parse_dt(d, mo, y, hh, mm)
                    sender, text = split_sender_text(rest)
                else:
                    d, mo, y, hh, mm, _ss, rest = g
                    dt = parse_dt(d, mo, y, hh, mm)
                    sender, text = split_sender_text(rest)

                if cur:
                    msgs.append(cur)
                cur = Message(dt=dt, sender=sender, text=text)
            else:
                if cur:
                    cur.text += "\n" + line

    if cur:
        msgs.append(cur)

    msgs.sort(key=lambda x: x.dt)
    return msgs

# ----------------------------
# Heurísticas "parte"
# ----------------------------

SHIFT_HINTS = [
    "guardia", "turno", "06 a 14", "6 a 14", "14 a 22", "22 a 06", "22 a 6",
    "manana", "mañana", "tarde", "noche"
]

UNIT_PATTERNS = [
    ("Cuidados Paliativos", re.compile(r"\b(paliativos|cuidados paliativos|paliat)\b", re.I)),
    ("Planta Baja", re.compile(r"\b(planta baja|pb)\b", re.I)),
    ("Planta Alta", re.compile(r"\b(planta alta|pa)\b", re.I)),
]

def infer_unit(text: str) -> str:
    for unit, pat in UNIT_PATTERNS:
        if pat.search(text):
            return unit
    return ""

def infer_shift_label(dt: datetime, text: str) -> str:
    t = text.lower()
    if "06" in t and "14" in t:
        return "Guardia de 06 a 14hs"
    if "14" in t and "22" in t:
        return "Guardia de 14 a 22hs"
    if "22" in t and ("06" in t or "6" in t):
        return "Guardia de 22 a 06hs"

    h = dt.hour
    if 6 <= h < 14:
        return "Guardia de 06 a 14hs"
    if 14 <= h < 22:
        return "Guardia de 14 a 22hs"
    return "Guardia de 22 a 06hs"

VITAL_KW = re.compile(r"\b(TA|SAT|SpO2|T(?:emp)?|diu(?:resis)?|gluc(?:osa)?|fc|fr)\b", re.I)

def looks_like_shift_report(text: str) -> bool:
    t = text.lower()
    score = 0
    for h in SHIFT_HINTS:
        if h in t:
            score += 1
    if "\n" in text:
        score += 1
    if VITAL_KW.search(text):
        score += 2

    # bonus: varias líneas con formato paciente
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    cand = 0
    for ln in lines:
        if re.match(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+(?:\s+[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+){0,3}\s*[:\-–]\s+.*$", ln):
            cand += 1
    if cand >= 3:
        score += 2

    return score >= 3


STOP_TAIL_TOKENS = {
    "continua", "continúa", "continuan", "continúan",
    "con", "al", "a", "del", "de", "la", "el",
    "comienzo", "inicio", "comienza", "comenzo", "comenzó",
    "ingreso", "ingresó", "ingresa",
}

def trim_name_tail(name_part: str) -> str:
    toks = (name_part or "").strip().split()
    if not toks:
        return name_part
    # ir sacando tokens finales “basura”
    while toks:
        last_norm = norm(toks[-1])
        if last_norm in STOP_TAIL_TOKENS:
            toks.pop()
            continue
        # caso "al comienzo" -> si queda "... al" y antes se sacó "comienzo"
        if len(toks[-1]) <= 2 and last_norm in {"a","al","de","del","la","el"}:
            toks.pop()
            continue
        break
    return " ".join(toks).strip()

# ----------------------------
# Parse líneas paciente (mejorado)
# ----------------------------

RE_TA  = re.compile(r"\bTA\s*[:=]?\s*(\d{2,3})\s*[/\-]\s*(\d{2,3})", re.I)
RE_SAT = re.compile(r"\bSAT\s*[:=]?\s*(\d{2,3})(?:\s*[/\-]\s*(\d{2,3}))?", re.I)
RE_T   = re.compile(r"\bT\s*[:=]?\s*(\d{2})(?:[.,](\d))?", re.I)
RE_DIU = re.compile(r"\bdiu(?:resis)?\s*[:=]?\s*(\d{1,5})", re.I)
RE_GLU = re.compile(r"\bgluc(?:osa)?\s*[:=]?\s*(\d{2,4})", re.I)
RE_FC  = re.compile(r"\bFC\s*[:=]?\s*(\d{2,3})", re.I)
RE_FR  = re.compile(r"\bFR\s*[:=]?\s*(\d{1,2})", re.I)

def extract_vitals(line: str) -> Tuple[Dict[str, Any], str]:
    vit: Dict[str, Any] = {}

    m = RE_TA.search(line)
    if m: vit["ta_sis"], vit["ta_dia"] = int(m.group(1)), int(m.group(2))

    m = RE_SAT.search(line)
    if m:
        vit["sat"] = int(m.group(1))
        if m.group(2):
            v2 = int(m.group(2))
            if v2 <= 220:
                vit["fc"] = v2

    m = RE_T.search(line)
    if m:
        vit["temp_c"] = float(f"{m.group(1)}.{m.group(2) or 0}")

    m = RE_DIU.search(line)
    if m: vit["diuresis_ml"] = int(m.group(1))

    m = RE_GLU.search(line)
    if m: vit["glucosa_mgdl"] = int(m.group(1))

    m = RE_FC.search(line)
    if m and "fc" not in vit: vit["fc"] = int(m.group(1))

    m = RE_FR.search(line)
    if m: vit["fr"] = int(m.group(1))

    cleaned = line
    for pat in [RE_TA, RE_SAT, RE_T, RE_DIU, RE_GLU, RE_FC, RE_FR]:
        cleaned = pat.sub("", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" .;-")
    return vit, cleaned

# STOPWORDS para evitar falsos pacientes ("Se", "Pasa", etc.)
STOP_FIRST_TOKENS = {
    "se","le","la","el","los","las","de","del","y","o","a","en","con","sin",
    "hoy","ayer","manana","mañana","tarde","noche",
    "pasa","queda","continua","continúa","refiere","presenta",
    "señala","realiza","coloca","retira","cambia","administra","control","controles",
    "medicacion","medicación","depos","depo","dep","observaciones","obs",
    "se","se","se",  # (intencional, suele venir muchísimo)
    "sr","sra","sres","sras",
}

# líneas claramente no-paciente aunque cumplan regex
BANNED_NAME_TOKENS = {
    "sonda","via","vía","curacion","curación","cura","pañal","panal","crema","nebu","nbz",
    "oxigeno","oxígeno","dieta","colacion","colación","glucosa","ta","sat","t",
}

def looks_like_person_name(name_part: str, allow_single_word: bool) -> bool:
    raw = (name_part or "").strip()
    if not raw:
        return False

    n = norm(raw)
    toks = n.split()
    if not toks:
        return False

    if toks[0] in STOP_FIRST_TOKENS:
        return False
    if toks[0] in BANNED_NAME_TOKENS:
        return False

    # 1 palabra: MUY riesgoso => solo si se permite y es "larga"
    if len(toks) == 1:
        return allow_single_word and len(toks[0]) >= 4 and toks[0] not in STOP_FIRST_TOKENS

    # 2-4 palabras: al menos una con contenido
    return any(len(t) >= 3 for t in toks)

STOP_TAIL_TOKENS = {
    "continua", "continúa", "continuan", "continúan",
    "con", "al", "a", "del", "de", "la", "el",
    "comienzo", "inicio", "comienza", "comenzo", "comenzó",
    "ingreso", "ingresó", "ingresa",
}

def trim_name_tail(name_part: str) -> str:
    toks = (name_part or "").strip().split()
    if not toks:
        return (name_part or "").strip()

    while toks:
        last_norm = norm(toks[-1])
        if last_norm in STOP_TAIL_TOKENS:
            toks.pop()
            continue
        # quita conectores sueltos al final
        if len(toks[-1]) <= 2 and last_norm in {"a", "al", "de", "del", "la", "el"}:
            toks.pop()
            continue
        break

    return " ".join(toks).strip()

def split_patient_line(line: str) -> Optional[Tuple[str, str]]:
    s = line.strip()
    if not s:
        return None

    # limpiar bullets/numeración
    s = re.sub(r"^\s*[\-\•\*]\s*", "", s)
    s = re.sub(r"^\s*\d+\)\s*", "", s)

    # notas globales típicas
    if re.match(r"^(depos?|dep:|depo:|medicacion|medicación|observaciones?|obs)\b", s, re.I):
        return None

    # Si la línea empieza directamente con signos, no es paciente
    if re.match(r"^(ta|sat|t|fc|fr|diu|glucosa)\b", norm(s), re.I):
        return None

    # Caso "Nombre Apellido: ..."
    m = re.match(
        r"^([A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+(?:\s+[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+){0,3})\s*[:\-–]\s*(.*)$",
        s
    )
    if m:
        name_part = trim_name_tail(m.group(1).strip())
        rest = m.group(2).strip()
        if not looks_like_person_name(name_part, allow_single_word=False):
            return None
        return name_part, rest

    # Caso "Horacio TA 120/70..." o "Horacio pasa bien"
    if VITAL_KW.search(s) or re.search(
        r"\b(pasa|duerme|somnol|inestable|almuerza|desayuna|come|no acepta|nbz|nebu|vom|dolor|via|vía)\b",
        s,
        re.I
    ):
        kw = re.search(
            r"\b(TA|SAT|SpO2|T|diu|gluc|pasa|duerme|somnol|inestable|almuerza|desayuna|come|no acepta|nbz|nebu|vom|dolor|via|vía)\b",
            s,
            re.I
        )
        if kw and kw.start() > 1:
            name_part = trim_name_tail(s[:kw.start()].strip())
            rest = s[kw.start():].strip()

            allow_single = False                 # ✅ SIEMPRE inicializada
            if VITAL_KW.search(rest):
                allow_single = True              # ✅ se habilita sólo si hay vitales

            if re.match(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+(?:\s+[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+){0,3}$", name_part):
                if not looks_like_person_name(name_part, allow_single_word=allow_single):
                    return None
                return name_part, rest
          
# ----------------------------
# Clustering nombres
# ----------------------------

def cluster_names(raw_names: List[str], sim_threshold: float) -> Tuple[Dict[str, str], Dict[str, List[str]]]:
    canonical: List[str] = []
    alias_to_canon: Dict[str, str] = {}
    canon_to_aliases: Dict[str, List[str]] = {}

    for raw in raw_names:
        rn = (raw or "").strip()
        if not rn:
            continue
        n = norm(rn)

        best = None
        best_score = 0.0
        for c in canonical:
            sc = name_similarity(rn, c)
            if sc > best_score:
                best_score = sc
                best = c

        if best and best_score >= sim_threshold:
            alias_to_canon[rn] = best
            canon_to_aliases.setdefault(best, []).append(rn)
        else:
            canonical.append(rn)
            alias_to_canon[rn] = rn
            canon_to_aliases.setdefault(rn, []).append(rn)

    return alias_to_canon, canon_to_aliases

def stable_id(prefix: str, canon: str) -> str:
    base = norm(canon)
    h = 0
    for ch in base:
        h = (h * 31 + ord(ch)) % 10_000_000
    return f"{prefix}_{h:07d}"

def token_prefix_match(a: List[str], b: List[str]) -> bool:
    # True si algún token es prefijo del otro (>=3 letras): "arg" ~ "argentino"
    for x in a:
        if len(x) < 3:
            continue
        for y in b:
            if x == y:
                return True
            if y.startswith(x) or x.startswith(y):
                return True
    return False

COMMON_NAME_TOKENS = {
    "maria","jose","juan","ana","rosa","carmen","marta","norma","beatriz","graciela",
    "hector","daniel","cristina","argentina","arg","lidia","lydia","paulina",
}

def informative_tokens(tokens: List[str]) -> List[str]:
    return [t for t in tokens if len(t) >= 4 and t not in COMMON_NAME_TOKENS]

def token_prefix_match(a: List[str], b: List[str]) -> bool:
    for x in a:
        if len(x) < 3:
            continue
        for y in b:
            if y.startswith(x) or x.startswith(y):
                return True
    return False
COMMON_TOKENS = {
    "maria","jose","juan","ana","rosa","carmen","marta","norma","beatriz","graciela",
    "hector","daniel","cristina","raquel","lidia","lydia","paulina","fernando"
}

def key_tokens(name: str) -> List[str]:
    """Tokens 'fuertes' para identificar paciente."""
    t = name_tokens(name)
    if not t:
        return []
    out = []
    # primer y último token (suele ser apellido/orden invertido)
    out.extend([t[0], t[-1]])
    # tokens largos (más identificatorios)
    out.extend([x for x in t if len(x) >= 5])
    # limpiar comunes y duplicados
    out = [x for x in out if x not in COMMON_TOKENS]
    return list(dict.fromkeys(out))

def token_equiv(a: str, b: str) -> bool:
    """Equivalencia suave: exact o prefijo (>=4) para abreviaturas/typos leves."""
    if a == b:
        return True
    if len(a) >= 4 and b.startswith(a):
        return True
    if len(b) >= 4 and a.startswith(b):
        return True
    return False

def fold_rare_into_active(
    patients_master_all: List[Dict[str, Any]],
    active_size: int,
    max_mentions_to_fold: int = 3,
    min_score: float = 0.82,
) -> Dict[str, str]:
    """
    Remapea variantes raras hacia el padrón activo usando:
      - score de nombre (name_similarity)
      - y un token clave compartido que sea "casi único" en el padrón
    """
    active = patients_master_all[:active_size]
    active_items = []
    token_freq: Dict[str, int] = {}

    # index activo + frecuencia de tokens clave
    for p in active:
        pid = p["patient_id"]
        cname = p["canonical_name"]
        kt = key_tokens(cname)
        active_items.append((pid, cname, kt))
        for tok in kt:
            token_freq[tok] = token_freq.get(tok, 0) + 1

    remap: Dict[str, str] = {}

    for p in patients_master_all[active_size:]:
        mentions = int(p.get("mentions") or 0)
        if mentions > max_mentions_to_fold:
            continue

        pid = p["patient_id"]
        name = p["canonical_name"]
        kt = key_tokens(name)
        if not kt:
            continue

        best_id = None
        best_sc = 0.0

        for aid, aname, akt in active_items:
            # buscar token clave compartido (equivalencia suave)
            shared = []
            for x in kt:
                for y in akt:
                    if token_equiv(x, y):
                        shared.append((x, y))
                        break

            if not shared:
                continue

            sc = name_similarity(name, aname)
            if sc < min_score:
                continue

            # regla de seguridad:
            # al menos un token compartido que sea único (freq==1) en el padrón activo,
            # o score muy alto.
            unique_shared = any(token_freq.get(y, 0) == 1 or token_freq.get(x, 0) == 1 for x, y in shared)
            very_high = sc >= 0.88

            if unique_shared or very_high:
                if sc > best_sc:
                    best_sc = sc
                    best_id = aid

        if best_id:
            remap[pid] = best_id

    return remap
# ----------------------------
# Dataset builder
# ----------------------------
def build_dataset(
    txt_path: str,
    output_path: str,
    expected_active_patients: int = 80,
    expected_nurses_approx: int = 50,
) -> Dict[str, Any]:
    msgs = parse_export(txt_path)
    if not msgs:
        data = {
            "meta": {},
            "shifts": [],
            "patients_master": [],
            "nurses_master": [],
            "daily_patient_summaries_active": [],
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return data

    last_dt = msgs[-1].dt
    cutoff = minus_2_months(last_dt)
    window = [m for m in msgs if m.dt >= cutoff]

    shift_msgs = [m for m in window if looks_like_shift_report(m.text)]

    # 1) cluster nurses primero
    nurse_raw_all: List[str] = [m.sender for m in shift_msgs if m.sender]
    alias_to_nurse, nurse_to_aliases = cluster_names(nurse_raw_all, sim_threshold=0.90)

    nurse_norm_set = set()
    for canon, aliases in nurse_to_aliases.items():
        nurse_norm_set.add(norm(canon))
        for a in aliases:
            nurse_norm_set.add(norm(a))

    nurse_id_map = {canon: stable_id("nurse", canon) for canon in nurse_to_aliases.keys()}

    # 2) parse shifts + juntar pacientes
    patient_raw_all: List[str] = []
    shifts: List[Dict[str, Any]] = []

    filtered_as_note = 0
    filtered_as_nurse = 0

    for m in shift_msgs:
        unit = infer_unit(m.text)
        shift_label = infer_shift_label(m.dt, m.text)

        lines = [ln.strip() for ln in m.text.splitlines() if ln.strip()]
        raw_header = lines[0] if lines else ""

        patients: List[Dict[str, Any]] = []
        shift_notes: List[str] = []

        sender_norm = norm(m.sender)

        for ln in lines:
            if ln.lower().startswith(("guardia", "turno")):
                continue

            if re.match(r"^(depos?|dep:|depo:)\b", ln, re.I):
                shift_notes.append(ln)
                continue

            p = split_patient_line(ln)
            if not p:
                shift_notes.append(ln)
                continue

            patient_raw, rest = p

            pn = norm(patient_raw)
            if pn == sender_norm or pn in nurse_norm_set:
                filtered_as_nurse += 1
                shift_notes.append(f"(Línea firmante/staff) {patient_raw}: {rest}".strip())
                continue

            vitals, cleaned_note = extract_vitals(rest)

            if not looks_like_person_name(patient_raw, allow_single_word=bool(vitals)):
                filtered_as_note += 1
                shift_notes.append(f"(Línea no-paciente) {patient_raw}: {cleaned_note or rest}".strip())
                continue

            patient_raw_all.append(patient_raw)
            patients.append({
                "patient": patient_raw,
                "patient_id": None,
                "vitals": vitals,
                "note": cleaned_note,
            })

        pb = m.sender or ""
        canon_n = alias_to_nurse.get(pb, pb)
        posted_by_id = nurse_id_map.get(canon_n)

        shifts.append({
            "shift_id": None,
            "posted_by": canon_n,
            "posted_by_id": posted_by_id,
            "reported_by": None,
            "sent_at": m.dt.isoformat(timespec="minutes"),
            "shift_label": shift_label,
            "unit": unit,
            "raw_header": raw_header,
            "patients": patients,
            "shift_notes": shift_notes,
        })

    # 3) cluster pacientes
    alias_to_patient, patient_to_aliases = cluster_names(patient_raw_all, sim_threshold=0.87)
    patient_id_map = {canon: stable_id("pat", canon) for canon in patient_to_aliases.keys()}

    # completar shifts con canonical + ids
    for sh in shifts:
        sh["shift_id"] = f'{sh["sent_at"]}_{norm(sh["posted_by"]).replace(" ", "")[:24]}'
        for p in sh["patients"]:
            canon_p = alias_to_patient.get(p["patient"], p["patient"])
            p["patient"] = canon_p
            p["patient_id"] = patient_id_map.get(canon_p)

    # stats iniciales
    counts: Dict[str, int] = {}
    first_seen: Dict[str, str] = {}
    last_seen: Dict[str, str] = {}

    for sh in shifts:
        ts = sh["sent_at"]
        for p in sh["patients"]:
            pid = p.get("patient_id")
            if not pid:
                continue
            counts[pid] = counts.get(pid, 0) + 1
            first_seen.setdefault(pid, ts)
            last_seen[pid] = ts

    patients_master_all: List[Dict[str, Any]] = []
    for canon, aliases in patient_to_aliases.items():
        pid = patient_id_map.get(canon)
        if not pid:
            continue
        patients_master_all.append({
            "patient_id": pid,
            "canonical_name": canon,
            "aliases": sorted(set(aliases)),
            "first_seen": first_seen.get(pid),
            "last_seen": last_seen.get(pid),
            "mentions": counts.get(pid, 0),
        })

    patients_master_all.sort(key=lambda x: (-x["mentions"], x["canonical_name"]))
    def debug_fold_candidates(patients_master_all, active_size=80, k=20):
        active = patients_master_all[:active_size]
        active_items = [(p["patient_id"], p["canonical_name"]) for p in active]

        shown = 0
        for p in patients_master_all[active_size:]:
            if int(p.get("mentions") or 0) > 3:
                continue
            name = p["canonical_name"]
            best = ("", "", 0.0)
            for aid, aname in active_items:
                sc = name_similarity(name, aname)
                if sc > best[2]:
                    best = (aid, aname, sc)
            print(f"[RARE] {name} (m={p.get('mentions')}) -> best={best[1]} score={best[2]:.3f}")
            shown += 1
            if shown >= k:
                break
    debug_fold_candidates(patients_master_all)

    # ---- FOLD: variantes raras -> padrón activo ----
    active_size = min(expected_active_patients, len(patients_master_all))
    remap = fold_rare_into_active(
        patients_master_all,
        active_size=active_size,
        max_mentions_to_fold=3,
        min_score=0.82,
    )

    if remap:
        # remap IDs dentro de shifts
        for sh in shifts:
            for it in sh["patients"]:
                pid = it.get("patient_id")
                if pid in remap:
                    it["patient_id"] = remap[pid]

        # id -> canonical
        id_to_canon = {pid: canon for canon, pid in patient_id_map.items()}

        folded_canons = set()
        for old_pid, new_pid in remap.items():
            old_canon = id_to_canon.get(old_pid)
            new_canon = id_to_canon.get(new_pid)
            if old_canon and new_canon and old_canon != new_canon:
                folded_canons.add(old_canon)
                patient_to_aliases.setdefault(new_canon, [])
                patient_to_aliases[new_canon].extend([old_canon] + (patient_to_aliases.get(old_canon) or []))

        # recomputar stats post-remap
        counts = {}
        first_seen = {}
        last_seen = {}
        for sh in shifts:
            ts = sh["sent_at"]
            for p in sh["patients"]:
                pid = p.get("patient_id")
                if not pid:
                    continue
                counts[pid] = counts.get(pid, 0) + 1
                first_seen.setdefault(pid, ts)
                last_seen[pid] = ts

        # reconstruir patients_master_all sin duplicados folded
        patients_master_all = []
        for canon, aliases in patient_to_aliases.items():
            if canon in folded_canons:
                continue
            pid = patient_id_map.get(canon)
            if not pid:
                continue
            mentions = counts.get(pid, 0)
            if mentions <= 0:
                continue
            patients_master_all.append({
                "patient_id": pid,
                "canonical_name": canon,
                "aliases": sorted(set(aliases)),
                "first_seen": first_seen.get(pid),
                "last_seen": last_seen.get(pid),
                "mentions": mentions,
            })

        patients_master_all.sort(key=lambda x: (-x["mentions"], x["canonical_name"]))

    # 4) recortar a padrón activo (~80) y mover extras a shift_notes (SIEMPRE, haya remap o no)
    patients_master = patients_master_all[:expected_active_patients]
    active_ids = {p["patient_id"] for p in patients_master}

    moved_out_of_roster = 0
    for sh in shifts:
        kept = []
        for p in sh["patients"]:
            if p.get("patient_id") in active_ids:
                kept.append(p)
            else:
                moved_out_of_roster += 1
                note = (p.get("note") or "").strip()
                sh["shift_notes"].append(f"(Fuera de padrón) {p.get('patient','')}: {note}".strip())
        sh["patients"] = kept

    # master nurses
    nurse_counts: Dict[str, int] = {}
    for sh in shifts:
        nid = sh.get("posted_by_id")
        if nid:
            nurse_counts[nid] = nurse_counts.get(nid, 0) + 1

    nurses_master = []
    for canon, aliases in nurse_to_aliases.items():
        nid = nurse_id_map[canon]
        nurses_master.append({
            "nurse_id": nid,
            "canonical_name": canon,
            "aliases": sorted(set(aliases)),
            "shift_posts": nurse_counts.get(nid, 0),
        })
    nurses_master.sort(key=lambda x: (-x["shift_posts"], x["canonical_name"]))

    daily = build_daily_summaries(shifts, active_ids)

    data = {
        "meta": {
            "last_message_at": last_dt.isoformat(timespec="minutes"),
            "cutoff_at": cutoff.isoformat(timespec="minutes"),
            "window_messages": len(window),
            "shift_messages_detected": len(shifts),
            "expected_active_patients": expected_active_patients,
            "expected_nurses_approx": expected_nurses_approx,
            "filtered_as_note": filtered_as_note,
            "filtered_as_nurse": filtered_as_nurse,
            "moved_out_of_roster": moved_out_of_roster,
            "patients_master_all_count": len(patients_master_all),
            "remap_count": len(remap),
        },
        "shifts": shifts,
        "patients_master": patients_master,
        "nurses_master": nurses_master,
        "daily_patient_summaries_active": daily,
        "patients_master_all": patients_master_all,  # debug/auditoría
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return data

def build_daily_summaries(shifts: List[Dict[str, Any]], active_patient_ids: set) -> List[Dict[str, Any]]:
    bucket: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}

    for sh in shifts:
        day = (sh.get("sent_at") or "")[:10]
        for p in sh.get("patients", []) or []:
            pid = p.get("patient_id")
            if not pid or pid not in active_patient_ids:
                continue
            bucket.setdefault((day, pid), []).append({
                "sent_at": sh.get("sent_at"),
                "shift_label": sh.get("shift_label", ""),
                "unit": sh.get("unit", ""),
                "note": (p.get("note") or "").strip(),
                "vitals": p.get("vitals") or {},
            })

    out = []
    for (day, pid), entries in bucket.items():
        entries.sort(key=lambda x: x.get("sent_at") or "")
        parts = []
        merged_vitals: Dict[str, Any] = {}

        for e in entries:
            note = e.get("note", "")
            if note:
                prefix = e.get("shift_label") or "Parte"
                if e.get("unit"):
                    prefix += f" ({e['unit']})"
                parts.append(f"{prefix}: {note}")
            merged_vitals.update(e.get("vitals") or {})

        friendly = " / ".join(parts) if parts else "Sin novedades consignadas por enfermería en el período registrado."

        vit_txt = []
        if "ta_sis" in merged_vitals and "ta_dia" in merged_vitals:
            vit_txt.append(f"TA {merged_vitals['ta_sis']}/{merged_vitals['ta_dia']}")
        if "sat" in merged_vitals:
            if "fc" in merged_vitals:
                vit_txt.append(f"SAT {merged_vitals['sat']} (pulso {merged_vitals['fc']})")
            else:
                vit_txt.append(f"SAT {merged_vitals['sat']}")
        if "temp_c" in merged_vitals:
            vit_txt.append(f"T {float(merged_vitals['temp_c']):.1f}°C")
        if "glucosa_mgdl" in merged_vitals:
            vit_txt.append(f"Glucosa {merged_vitals['glucosa_mgdl']}")
        if "diuresis_ml" in merged_vitals:
            vit_txt.append(f"Diuresis {merged_vitals['diuresis_ml']} ml")

        if vit_txt:
            friendly += "\nSignos consignados: " + ", ".join(vit_txt) + "."

        out.append({
            "date": day,
            "patient_id": pid,
            "family_friendly_summary": friendly
        })

    out.sort(key=lambda x: (x["date"], x["patient_id"]))
    return out


# =====================================================================
# ROSTERS + ALIASES (curados)
#   - Basado en listado de enfermeras/pacientes + apodos provistos
#   - Evita "fold" automático inseguro que mezclaba pacientes parecidos
# =====================================================================

# ---- Enfermería (canonical + aliases explícitos) ----
KNOWN_NURSES: List[str] = [
    "Brisa Aranda",
    "Agustina Aristizabal",
    "Micaela Baca",
    "Lucía Bertoldi",
    "Sofia Boan",
    "María Cabrera",
    "Laura Cevallos",
    "Priscila Chaparro",
    "Romina Cuadra",
    "Julieta Díaz",
    "Camila Duarte",
    "Emiliano Esquivel",
    "Esteban Fernández",
    "Gianella Frank",
    "Marta Gavilán",
    "Yazmín González",
    "Micaela Herrera",
    "Ludmila Ledesma",
    "Yohana León",
    "Verónica López",
    "Barbara Martinez",
    "Candela Martinez",
    "Agustín Mendoza",
    "Nahir Merlo",
    "Bianca Nasimbera",
    "Renzo Noguera",
    "Jazmín Otondo",
    "Yanina Piedrabuena",
    "Nancy Rivero",
    "Antonella Robles",
    "Daiana Romero",
    "Valeria Saris",
    "Camila Valenzuela",
    "Daniela Vera",
    "Sharon",
    "Gabriela",
    "Jorgelina",
    "Enfermeria 3er S",
    "Enfermeria PA",
]

NURSE_ALIASES: Dict[str, List[str]] = {
    "Emiliano Esquivel": ["Esquivel.e.", "Esquivel E", "Esquivel.e"],
    "Yazmín González": ["Gonzalez Yazmin", "González Yazmín", "Gonzalez, Yazmin"],
    "Yohana León": ["JOHANA LEÓN", "Yohana LEON", "León Yohana", "Leon Yohana"],
    "Barbara Martinez": ["Martinez Barbara", "Martinez Barbara."],
    "Bianca Nasimbera": ["Nasimbera Bianca", "Nasimbera Bianca."],
    "Antonella Robles": ["Roble Antonella", "Robles Antonella"],
    "Valeria Saris": ["Saris valeria", "Saris Valeria"],
}

# ---- Pacientes (canonical + aliases explícitos) ----
# Nota: incluimos además pacientes muy frecuentes detectados en los datos (p.ej. Noelia Gasparin, Carlos Alcántara, etc.)
KNOWN_PATIENTS: List[str] = [
    # Lista provista (y algunos completados por patrón observado)
    "Julio Abud",
    "Myriam Acevedo",
    "Eleuterio Acosta",
    "Isabel Acosta",
    "Martha Alderete",
    "Esther Alperin",
    "Angela Alvarez",
    "Marta Asiain",
    "Norma Bolzan",
    "Susana Piani",
    "Mirta Brumatti",
    "Lía Cagnani",
    "Marta Capellino",
    "Mercedes Cecotti",
    "Candida Chaparro",
    "Erica Dorchs",
    "Esther Elseser",
    "Alicia Felici",
    "Nilda Ferreyra",
    "Norberto Firpo",
    "Gualberto Firpo",
    "Fischer Bassi",
    "Beatriz Fonaroff",
    "Rita Fraga",
    "Lidya Franscisti",
    "Raquel Gainza",
    "Irma Gamarci",
    "Beatriz Garcilazo",
    "Mario Gervasoni",
    "Fernando Gonzalez",
    "Marta González",
    "Irma Herrera",
    "Marcelo Krupnick",
    "Dora Kraft",
    "Aída Loza",
    "Horacio Mangioni",
    "Hector Mariani",
    "Bruna Martinez",
    "Marta Mendoza",
    "Stella Mohor",
    "Elsa Moles",
    "Nina Oberti",
    "Celia Onna",
    "Andrés Padin",
    "Cristina Paez",
    "María del Carmen Paradisso",
    "Sofía Pasgall",
    "Emma Perez",
    "Alcira Piriz",
    "Ada Plaza",
    "Mónica Quinteros",
    "Teresa Redruello",
    "Griselda Riatto",
    "Alicia Rodriguez",
    "Antonia Rodriguez",
    "Daniel Rodriguez",
    "Catalina Salvador",
    "José Schell",
    "Amalia Sisneros",
    "Graciela Smith",
    "Clydez Spritz",
    "Federico Tinta",
    "Brunilda Venturini",
    "Celia Zlotinzki",
    "Carlos Zuiani",
    "Delia Zuiani",
    "José Zunino",

    # Nombres sueltos / apodos que aparecen como paciente
    "Aurora",
    "Mabel",

    # Apodos/variantes adicionales provistas
    "Marta Yogi",
    "Marta Tomassi",
    "Raquel Praino",
    "Silvia Rios",
    "Jorge Rios",
    "Ana Gusa",
    "Mabel Martinez",
    "Marta Ellembergle",
    "Ignacio Luraschi",
    "Alcain",

    # Pacientes muy frecuentes en el export (no estaban en la lista, pero aparecen claro en los partes)
    "Noelia Gasparin",
    "Carlos Alcantara",
    "Victoria Longo",
    "Teresa Farall",
    "Gladys Dayub",
    "Ursula Ceparo",
    "Gabriel Matharan",
    "Eduardo Valente",
    "Jorge Pedrotti",
    "Lidia Squeff",
    "Pocha Squeff",
    "Lita Fosatti",
    "Ofelia Fosatti",
    "Carlos Sione",
    "Máximo Sione",
    "Jane Maravankin",
    "Julio Gamarci",
    "José Firpo",
    "Mirta Zatti",  # aparece mucho; si NO corresponde, borrar y poner como alias de otra
]

PATIENT_ALIASES: Dict[str, List[str]] = {
    "Julio Abud": ["Abud Julio", "Adub Julio", "Adub, Julio", "Abud, Julio"],
    "Myriam Acevedo": ["Acevedo Myriam", "Acevedo, Myriam", "Aldana", "Mirian", "Myriam"],
    "Eleuterio Acosta": ["Acosta Eleuterio", "Acosta, Eleuterio", "Lute", "Acosta Lute"],
    "Marta Asiain": ["Asain", "Asiain Marta", "Asain Marta"],
    "Marta Capellino": ["Capellino Lucila", "Capellimo Lucila", "Capellino Marta", "Capellino, Marta"],
    "Mercedes Cecotti": ["Cecotti Bochi", "Bochi", "Cecotti, Mercedes", "Cecotti Mercedes"],
    "Alicia Felici": ["Felici aliciaS", "Felici", "FELICI", "FELICI temp"],
    "Norberto Firpo": ["Firpo Beto", "Firpo beto", "Beto", "Beto Firpo", "Norberto Firpo", "Firpo"],
    "Beatriz Fonaroff": ["Fonaroff Bety", "Bety fornaroff", "Fonaroff Beba", "Beba", "Fonarof Beatriz", "Fonaroff", "Fonaroff Beatriz"],
    "Lidya Franscisti": ["Francisti lidya", "Francisti lydia", "Francisti", "Frascisty lidia", "Fransisti lidya", "Franscisti Lidya", "Francisty", "Lidia Francisti", "Lidia Fransisti", "Fransisti Lidia"],
    "Irma Gamarci": ["García Irma", "Garcia Irma", "Gamarci Irma", "Gamarci, Irma"],
    "Mario Gervasoni": ["marion gervasoni", "Gerbasoni", "Gervasoni", "Gervasoni Mario", "Gerbazoni", "Gerbazoni Mario"],
    "Fernando Gonzalez": ["Gonzales Sergio", "Gonzales fernando", "Sergio González", "Gonzalez antonio", "Fernando Gonzáles", "González Fernandez", "Gonzalez Fernando no"],
    "Marta González": ["González Marta", "González Marta S N", "Marta Gonzales", "Marta Gonzalez"],
    "Graciela Smith": ["Smith", "Smith Graciela", "Basaldua Graciela", "Basaldua", "Basaldua graciela"],
    "Dora Kraft": ["Kraft Dora", "Dora Kraft", "Katz Dora", "Katz"],
    "Stella Mohor": ["Mohor Stella", "Mohor Stella sale", "Mohor Estela", "Estela Mohor", "Mohor", "Stella"],
    "Sofía Pasgall": ["sofia pasgall", "Pasgal", "Pasgall", "Pasgal Sofía", "Pasgall Sofía"],
    "José Schell": ["SCHELL JOSE", "Shell", "Schell", "Shell Jose"],
    "Federico Tinta": ["Tinta", "Tinta Fede", "Tinta fede", "Tinta federico", "Fede", "Tinta fede continúa c cóctel."],
    "Brunilda Venturini": ["Venturini Brunilda", "Brunilda", "Venturini"],
    "Delia Zuiani": ["ZUIANI DELIA", "Delia Z", "Zuiani Delia", "Zuiani"],
    "Carlos Zuiani": ["ZUIANI CARLOS", "Zuiani Carlos", "Zuiani"],
    "Noelia Gasparin": ["Gasparin", "Gasparin Noelia", "Noelia gasparin", "Nohelia gasparin", "Noelia", "Gasparin y", "Noelia Gasparin"],
    "Carlos Alcantara": ["Alcántara", "Alcantara", "Alcántara Carlos", "Alcantara Carlos", "Carlos Alcantara"],
    "Victoria Longo": ["Longo Victoria", "Victoria Longo", "Longo"],
    "Teresa Farall": ["Farall Teresa", "Teresa farall", "China Farall", "Teress farall", "Farall"],
    "Gladys Dayub": ["Dayub", "Dayub Gladys", "Gladys Dayub"],
    "Ursula Ceparo": ["Ceparo", "Ursula ceparo", "Ursula Ceparo"],
    "Gabriel Matharan": ["Matharan", "Gabriel Matharan", "Matharan Gabriel"],
    "Eduardo Valente": ["Valente", "Eduardo valente", "Valente Eduardo"],
    "Jorge Pedrotti": ["Pedrotti", "Pedrotti Jorge", "Jorge Pedrotti", "garcilazo Pedrotti"],
    "Lidia Squeff": ["Squeff Lidia", "Squeff lidia", "Lidia Skeff", "Skeff Lidia", "skeff"],
    "Pocha Squeff": ["Squeff Pocha", "Squeff pocha", "Pocha"],
    "Lita Fosatti": ["FOSATTI LITA", "Fosatti Lita", "Lita Fosatti"],
    "Ofelia Fosatti": ["Fosatti Ofelia", "Ofelia Fosatti", "Fosati Ofelia"],
    "Carlos Sione": ["Sione Carlos", "Carlos sione", "SIONE CARLOS", "Sione C."],
    "Máximo Sione": ["Sione maximo", "SIONE máximo", "Maximo Sione", "MAXIMO SIONE", "SIONE MAXIMO", "Máximo sione"],
    "Jane Maravankin": ["Maravankin Jane", "Jane Maravankin", "Maravankin"],
    "Julio Gamarci": ["Gamarci Julio", "Julio Gamarci"],
    "José Firpo": ["Firpo jose", "José Firpo", "Firpo José"],
    # En datos aparece muchísimo "Zatti Mirta"
    "Mirta Zatti": ["Zatti Mirta", "Mirta zatti", "Zatti"],
    # Si en realidad "Zatti Mirta" era una variante de Brumatti, mantener esto:
    "Mirta Brumatti": ["Mirta Brumatti", "Brumatti Mirta", "Zatti Mirta", "Brumatti", "Mirta B", "Zatti"],    "Susana Piani": ["Piani", "Piani Susana", "Piani susana", "Susana", "Susana Piani"],
    "Catalina Salvador": ["Cata", "Catalina salvador", "Salvador Catalina de indica"],
    "Amalia Sisneros": ["Cisneros", "Sisneros", "Cisneros Amalia"],
    "Norma Bolzan": ["Bolsan", "Bolzan", "Bolsan Norma"],
    "Marcelo Krupnick": ["Marcelo K", "Krupnik", "Krupnick Marcelo"],
    "Celia Zlotinzki": ["Zlontinsky Celia", "Zlotinzky Celia", "Zlotinsky", "Zlotinzki Celia", "Celia Z", "Celia Z.", "Celia Zlotinzki", "Zlotinzki Celia Z"],
    "María del Carmen Paradisso": ["Paradiso", "Paradisso", "Paradiso M del Carmen", "Paradiso Maria del Carmen", "Paradiso Carmen", "María del Carmen"],
    "Fischer Bassi": ["Basi", "Bassi", "Fischer", "Fischer Bassi"],
    "Marta Ellembergle": ["Ellembergle", "Ellembergle Marta", "Ellembergle Marta."],
    "Raquel Praino": ["Praino", "Praino raquel"],
    "Alcain": ["Alcain"],
    "Clydez Spritz": ["Clydes", "Clyde", "Clide", "Clydez", "Spritz", "Spretz", "Spritz Clyde", "Spritz Clydes"],
    "Candida Chaparro": ["Chaparro Cati", "Chaparro caty", "Chaparro", "Candi Chaparro"],
    "Mabel Martinez": ["Martinez Mabel", "Mabel Martinez", "Martinez, Mabel"],
    "Ana Gusa": ["Gusa", "Gusa Ana", "Gusa Ana Maria", "Ana Maria Gusa", "Ana Gusa"],
    "Jorge Rios": ["Rios Jorge", "Ríos Jorge", "Jorge Rios", "Rios"],
    "Silvia Rios": ["Rios Silvia", "Ríos Silvia", "Silvia Rios"],
    "Elsa Moles": ["Moles", "Joles Elsa", "Joles", "Moles Elsa"],

}

# Palabras que frecuentemente se "pegan" al nombre y NO son parte del nombre (p.ej. "Gasparin poco")
NAME_TAIL_NOTE_WORDS = {"poco", "continua", "continúa", "cont", "c", "cn", "con"}

def _name_forms(name: str) -> List[str]:
    """Genera variantes de escritura para matching exacto (no fuzzy)."""
    n = (name or "").strip()
    if not n:
        return []
    toks = n.split()
    forms = {n}

    if len(toks) >= 2:
        forms.add(" ".join([toks[-1]] + toks[:-1]))  # Apellido Nombre
        forms.add(f"{toks[-1]}, {' '.join(toks[:-1])}")  # Apellido, Nombre

    # sin dobles espacios
    forms = {re.sub(r"\s+", " ", x).strip() for x in forms if x.strip()}
    return sorted(forms)

def build_alias_index(canon_list: List[str], canon_to_aliases: Dict[str, List[str]]) -> Tuple[Dict[str, str], set]:
    """
    Devuelve:
      - alias_norm -> canonical
      - set(alias_norm) ambiguos (colisionan)
    """
    tmp: Dict[str, str] = {}
    amb: set = set()

    def add(alias: str, canon: str):
        an = norm(alias)
        if not an:
            return
        if an in tmp and tmp[an] != canon:
            amb.add(an)
        else:
            tmp[an] = canon

    for canon in canon_list:
        for f in _name_forms(canon):
            add(f, canon)

    for canon, aliases in (canon_to_aliases or {}).items():
        # asegurar canon en roster
        if canon not in canon_list:
            canon_list.append(canon)
        for a in (aliases or []):
            add(a, canon)
            # también formas del alias, si tiene >=2 tokens
            for f in _name_forms(a):
                add(f, canon)

    # limpiar ambiguos
    out = {k: v for k, v in tmp.items() if k not in amb}
    return out, amb

def build_token_indices(canon_list: List[str]) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    first_idx: Dict[str, List[str]] = {}
    last_idx: Dict[str, List[str]] = {}
    for c in canon_list:
        t = name_tokens(c)
        if not t:
            continue
        first_idx.setdefault(t[0], []).append(c)
        last_idx.setdefault(t[-1], []).append(c)
    return first_idx, last_idx

def best_fuzzy_match(raw: str, canon_list: List[str], min_score: float, require_gap: float = 0.05) -> Optional[str]:
    r = (raw or "").strip()
    if not r:
        return None
    rt = name_tokens(r)
    if not rt:
        return None

    # candidatos: comparten algún token "fuerte" (>=4) para no comparar contra todo
    strong = {t for t in rt if len(t) >= 4}
    candidates = []
    for c in canon_list:
        ct = name_tokens(c)
        if not ct:
            continue
        if strong and not (strong & {t for t in ct if len(t) >= 4}):
            continue
        candidates.append(c)
    if not candidates:
        candidates = canon_list

    best = None
    best_sc = 0.0
    second = 0.0
    for c in candidates:
        sc = name_similarity(r, c)
        if sc > best_sc:
            second = best_sc
            best_sc = sc
            best = c
        elif sc > second:
            second = sc

    if best and best_sc >= min_score and (best_sc - second) >= require_gap:
        return best
    return None

def canonicalize_nurse(raw: str, nurse_alias: Dict[str, str], nurse_roster: List[str]) -> str:
    rn = (raw or "").strip()
    if not rn:
        return ""
    nn = norm(rn)
    if nn in nurse_alias:
        return nurse_alias[nn]
    # fuzzy contra roster (solo si parece un nombre real)
    if looks_like_person_name(rn, allow_single_word=True):
        m = best_fuzzy_match(rn, nurse_roster, min_score=0.90, require_gap=0.04)
        if m:
            return m
    return rn

def _trim_tail_words(name_part: str) -> str:
    toks = (name_part or "").strip().split()
    while len(toks) >= 2 and norm(toks[-1]) in NAME_TAIL_NOTE_WORDS:
        toks.pop()
    return " ".join(toks).strip()

def canonicalize_patient(raw: str,
                         patient_alias: Dict[str, str],
                         patient_roster: List[str],
                         first_idx: Dict[str, List[str]],
                         last_idx: Dict[str, List[str]],
                         allow_fuzzy: bool = True) -> Optional[str]:
    rn = (raw or "").strip()
    if not rn:
        return None

    rn = _trim_tail_words(rn)
    nn = norm(rn)
    if nn in patient_alias:
        return patient_alias[nn]

    toks = name_tokens(rn)
    if not toks:
        return None

    # 1 token: usar índices por primer/último token si es único
    if len(toks) == 1:
        t = toks[0]
        cand = list(dict.fromkeys((last_idx.get(t, []) + first_idx.get(t, []))))
        if len(cand) == 1:
            return cand[0]
        return None

    # exact por formas canónicas (ya quedó cubierto por patient_alias)
    if not allow_fuzzy:
        return None

    # fuzzy
    m = best_fuzzy_match(rn, patient_roster, min_score=0.88, require_gap=0.05)
    return m

def resolve_shift_patients(items: List[Dict[str, Any]],
                           patient_alias: Dict[str, str],
                           patient_roster: List[str],
                           first_idx: Dict[str, List[str]],
                           last_idx: Dict[str, List[str]]) -> None:
    """
    items: [{"raw":..., "note":..., "vitals":..., "resolved":None}, ...]
    Resuelve in-place.
    """
    # Pass 1: alias/exacto/fuzzy (multi-token) + únicos (1 token)
    for it in items:
        raw = it.get("raw") or ""
        canon = canonicalize_patient(raw, patient_alias, patient_roster, first_idx, last_idx, allow_fuzzy=True)
        it["resolved"] = canon

    # Pass 2: backfill local dentro del mismo mensaje: si el raw (1 token) coincide con
    # primer/último token de EXACTAMENTE 1 paciente ya resuelto en ese shift => asignar
    resolved_canons = [it["resolved"] for it in items if it.get("resolved")]
    if not resolved_canons:
        return

    resolved_tokens = []
    for c in resolved_canons:
        t = name_tokens(c)
        if t:
            resolved_tokens.append((c, t[0], t[-1]))

    for it in items:
        if it.get("resolved"):
            continue
        raw = (it.get("raw") or "").strip()
        toks = name_tokens(raw)
        if len(toks) != 1:
            continue
        t = toks[0]
        cands = [c for (c, first, last) in resolved_tokens if t in {first, last}]
        if len(set(cands)) == 1:
            it["resolved"] = cands[0]

# =====================================================================
# OVERRIDES: infer_unit / infer_shift_label / split_patient_line / build_dataset
# =====================================================================

_UNIT_PATTERNS = [
    ("Cuidados paliativos", re.compile(r"\b(paliat|ucp|cuidados\s+paliativos)\b", re.I)),
    ("Planta alta", re.compile(r"\b(planta\s*alta|p\.?a\.?|\bpa\b|2do\s*piso|segundo\s*piso)\b", re.I)),
    ("Planta baja", re.compile(r"\b(planta\s*baja|p\.?b\.?|\bpb\b|1er\s*piso|primer\s*piso)\b", re.I)),
]

def infer_unit(text: str, sender: str = "") -> str:
    # prioridad: sender (muchos mensajes salen del grupo "Enfermeria PA/PB")
    s = f"{sender or ''} {text or ''}"
    for label, rx in _UNIT_PATTERNS:
        if rx.search(s):
            return label
    return ""

def infer_shift_label(dt: datetime, text: str = "") -> str:
    # si el texto trae TM/TT/TN o rangos horarios, usarlo
    header = (text or "").splitlines()[0] if (text or "").strip() else ""
    hnorm = norm(header)
    
    # Prioridad: rangos horarios explícitos en el header
    if "06" in hnorm and "14" in hnorm:
        return "Turno mañana"
    if "14" in hnorm and "22" in hnorm:
        return "Turno tarde"
    if "22" in hnorm and ("06" in hnorm or "06" in hnorm or "6" in hnorm):
        return "Turno noche"

    if re.search(r"\b(tm|t\.m|mañana)\b", hnorm):
        return "Turno mañana"
    if re.search(r"\b(tt|t\.t|tarde)\b", hnorm):
        return "Turno tarde"
    if re.search(r"\b(tn|t\.n|noche)\b", hnorm):
        return "Turno noche"
    
    # fallback por hora (según reglas: 06-14, 14-22, 22-06)
    h = dt.hour
    if 6 <= h < 14:
        return "Turno mañana"
    if 14 <= h < 22:
        return "Turno tarde"
    return "Turno noche"

def split_patient_line(line: str) -> Optional[Tuple[str, str]]:
    """
    Override: permite nombre de 1 token (apodo/apellido) si pasa filtros básicos.
    Además recorta palabras finales tipo 'poco', 'continúa', etc.
    """
    s = (line or "").strip()
    if not s:
        return None

    # limpiar bullets/numeración/emoji inicial simple
    s = re.sub(r"^\s*[\-\•\*\✔\✅\☑\👉\➡\📍\📌\👍\☘️\⛔]+\s*", "", s)
    s = re.sub(r"^\s*\d+\)\s*", "", s)

    # notas globales típicas
    if re.match(r"^(depos?|dep\+?|depo\+?:|medicacion|medicación|observaciones?|obs|signos\s+vitales|sv)\b", s, re.I):
        return None

    # Si la línea empieza directamente con signos, no es paciente
    if re.match(r"^(ta|sat|t|fc|fr|diu|glucosa)\b", norm(s), re.I):
        return None

    # Caso "Nombre Apellido: ..."
    m = re.match(
        r"^([A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+(?:\s+[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+){0,4})\s*[:\-–]\s*(.*)$",
        s
    )
    if m:
        name_part = _trim_tail_words(trim_name_tail(m.group(1).strip()))
        rest = m.group(2).strip()
        if not looks_like_person_name(name_part, allow_single_word=True):
            return None
        return name_part, rest

    # Caso "Horacio TA 120/70..." o "Sione duerme bien"
    if VITAL_KW.search(s) or re.search(
        r"\b(pasa|duerme|somnol|inestable|almuerza|desayuna|come|no acepta|nbz|nebu|vom|dolor|via|vía|hidrata|merienda|cena)\b",
        s,
        re.I
    ):
        kw = re.search(
            r"\b(TA|SAT|SpO2|T|diu|gluc|pasa|duerme|somnol|inestable|almuerza|desayuna|come|no acepta|nbz|nebu|vom|dolor|via|vía|hidrata|merienda|cena)\b",
            s,
            re.I
        )
        if kw and kw.start() > 1:
            name_part = _trim_tail_words(trim_name_tail(s[:kw.start()].strip()))
            rest = s[kw.start():].strip()

            allow_single = True  # override: habilitamos 1 token, luego se valida por roster/aliases
            if re.match(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+(?:\s+[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+){0,4}$", name_part):
                if not looks_like_person_name(name_part, allow_single_word=allow_single):
                    return None
                return name_part, rest

    return None

def build_dataset(
    txt_path: str,
    output_path: str,
    expected_active_patients: int = 80,
    expected_nurses_approx: int = 50,
    names_id_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Override (seguro):
      - Canonicaliza pacientes usando roster + aliases (NO fold agresivo)
      - Resuelve apodos/apellidos dentro del mismo mensaje (backfill local)
      - Deduce unidad (PB/PA/UCP) usando también el sender del grupo
    """
    msgs = parse_export(txt_path)
    if not msgs:
        data = {"meta": {}, "shifts": [], "patients_master": [], "nurses_master": [], "daily_patient_summaries_active": []}
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return data

    last_dt = msgs[-1].dt
    first_dt = msgs[0].dt
    cutoff = minus_2_months(last_dt)
    window = [m for m in msgs if m.dt >= cutoff]
    shift_msgs = [m for m in window if looks_like_shift_report(m.text)]

    # Cargar names-id.txt si se proporcionó
    names_db: Dict[str, Any] = {}
    if names_id_path:
        names_db = load_names_id(names_id_path)
        print(f"[INFO] names-id.txt cargado: {len(names_db.get('patient_id_map', {}))} pacientes, "
              f"{len(names_db.get('nurse_id_map', {}))} enfermeros")

    # Alias indices
    nurse_roster = list(KNOWN_NURSES)
    nurse_alias, nurse_amb = build_alias_index(nurse_roster, NURSE_ALIASES)

    patient_roster = list(KNOWN_PATIENTS)
    patient_alias, patient_amb = build_alias_index(patient_roster, PATIENT_ALIASES)
    first_idx, last_idx = build_token_indices(patient_roster)

    # Incorporar nombres de names-id.txt al índice de alias de enfermería para mejor filtrado de firmas
    if names_db:
        for nn_key in names_db.get("nurse_id_map", {}).keys():
            if nn_key not in nurse_alias:
                nurse_alias[nn_key] = nn_key # El valor no importa para is_probably_nurse_label

    shifts: List[Dict[str, Any]] = []

    # stats
    filtered_as_note = 0
    filtered_as_nurse = 0
    unmatched_patient_lines = 0
    total_vitals_count = 0
    total_lines_processed = 0

    # counts
    patient_counts: Dict[str, int] = {}
    patient_first_seen: Dict[str, str] = {}
    patient_last_seen: Dict[str, str] = {}
    patient_aliases_seen: Dict[str, set] = {}

    nurse_counts: Dict[str, int] = {}
    nurse_aliases_seen: Dict[str, set] = {}

    # helper: decide si una "etiqueta" parece enfermera
    def is_probably_nurse_label(label: str) -> bool:
        if not label:
            return False
        ln = norm(label)
        if ln in nurse_alias:
            return True
        # fuzzy contra roster si es claramente persona (evita confundir "enfermeria pa")
        if looks_like_person_name(label, allow_single_word=True):
            m = best_fuzzy_match(label, nurse_roster, min_score=0.92, require_gap=0.04)
            return bool(m)
        return False

    for m in shift_msgs:
        unit = infer_unit(m.text, m.sender)
        shift_label = infer_shift_label(m.dt, m.text)

        lines = [ln.strip() for ln in (m.text or "").splitlines() if ln.strip()]
        raw_header = lines[0] if lines else ""

        posted_by = canonicalize_nurse(m.sender, nurse_alias, nurse_roster) or (m.sender or "")
        # Intentar resolver ID desde names-id.txt, si no, usar hash estable
        if names_db:
            posted_by_id = resolve_id_from_names(
                posted_by,
                names_db["nurse_id_map"],
                names_db.get("nurse_alias_extra", {})
            ) or stable_id("nurse", posted_by)
        else:
            posted_by_id = stable_id("nurse", posted_by) if posted_by else None
        nurse_counts[posted_by_id] = nurse_counts.get(posted_by_id, 0) + 1 if posted_by_id else nurse_counts.get(posted_by_id, 0)

        if posted_by:
            nurse_aliases_seen.setdefault(posted_by, set()).add(m.sender)

        patient_items: List[Dict[str, Any]] = []
        shift_notes: List[str] = []

        sender_norm = norm(m.sender)

        last_patient_idx = -1
        for i, ln in enumerate(lines):
            if ln.lower().startswith(("guardia", "turno")):
                continue

            p = split_patient_line(ln)
            if not p:
                continue

            patient_raw, rest = p
            pn = norm(patient_raw)

            # Si es staff, ignoramos para el índice del último paciente
            if pn == sender_norm or is_probably_nurse_label(patient_raw):
                filtered_as_nurse += 1
                continue

            vitals, cleaned_note = extract_vitals(rest)
            total_lines_processed += 1
            if vitals:
                total_vitals_count += 1

            if not looks_like_person_name(patient_raw, allow_single_word=True):
                filtered_as_note += 1
                continue

            # Es un paciente válido
            last_patient_idx = i
            patient_items.append({
                "raw": patient_raw,
                "note": (cleaned_note or "").strip(),
                "vitals": vitals,
                "resolved": None,
            })

        # Capturar resumen de guardia (solo lo que sigue al último paciente)
        if last_patient_idx != -1:
            for tl in lines[last_patient_idx + 1:]:
                tl_strip = tl.strip()
                if not tl_strip:
                    continue
                # Filtrar si parece firma de enfermera
                if is_probably_nurse_label(tl_strip):
                    continue
                shift_notes.append(tl_strip)

        # Resolver pacientes dentro del shift
        resolve_shift_patients(patient_items, patient_alias, patient_roster, first_idx, last_idx)

        patients: List[Dict[str, Any]] = []
        sent_at = m.dt.isoformat(timespec="minutes")

        for it in patient_items:
            canon = it.get("resolved")
            if not canon:
                unmatched_patient_lines += 1
                continue

            # Usamos el NOMBRE canonical como clave para el conteo de frecuencia para evitar líos de IDs
            patients.append({
                "patient": canon,
                "patient_id": None, # se resolverá al final o in-place
                "vitals": it.get("vitals") or {},
                "note": (it.get("note") or "").strip(),
            })

            patient_counts[canon] = patient_counts.get(canon, 0) + 1
            pid_tmp = stable_id("pat", canon) # para primer/ultimo visto
            patient_first_seen.setdefault(pid_tmp, sent_at)
            patient_last_seen[pid_tmp] = sent_at
            patient_aliases_seen.setdefault(canon, set()).add(it.get("raw") or canon)

        shift_id = f'{sent_at}_{norm(posted_by).replace(" ", "")[:24]}'
        shifts.append({
            "shift_id": shift_id,
            "posted_by": posted_by,
            "posted_by_id": posted_by_id,
            "reported_by": None,
            "sent_at": sent_at,
            "shift_label": shift_label,
            "unit": unit,
            "raw_header": raw_header,
            "patients": patients,
            "shift_notes": shift_notes,
        })

    # ---- elegir padrón activo (~80) ----
    # candidates: todos los pacientes vistos (por pid o canon)
    # primero recolectamos los canes que tienen menciones
    observed_canons = [c for c, count in patient_counts.items() if count > 0]
    # mapear a (pid, canon, count)
    observed_data = []
    for c in observed_canons:
        pid = stable_id("pat", c)
        if names_db:
            resolved = resolve_id_from_names(c, names_db["patient_id_map"], names_db.get("patient_alias_extra", {}))
            if resolved: pid = resolved
        observed_data.append((pid, c, patient_counts.get(c, 0)))

    # ordenar por menciones
    observed_data.sort(key=lambda x: (-x[2], x[1]))

    # Seleccionar activos
    active_set = set() # pids activos
    active_canons = set()
    
    # 1. Prioridad: Los que tienen más menciones hasta llegar a expected_active_patients
    for pid, c, count in observed_data[:expected_active_patients]:
        active_set.add(pid)
        active_canons.add(c)

    # 2. Si sobran espacios, completar con el roster de nombres conocidos (del names-id.txt)
    if len(active_set) < expected_active_patients:
        for c in patient_roster:
            pid = stable_id("pat", c)
            if names_db:
                resolved = resolve_id_from_names(c, names_db["patient_id_map"], names_db.get("patient_alias_extra", {}))
                if resolved: pid = resolved
            
            if pid not in active_set:
                active_set.add(pid)
                active_canons.add(c)
            if len(active_set) >= expected_active_patients:
                break


    # filtrar shifts -> solo activos y asignar IDs finales
    moved_out_of_roster = 0
    for sh in shifts:
        kept = []
        for p in sh.get("patients", []):
            canon = p["patient"]
            # resolver final pid
            final_pid = stable_id("pat", canon)
            if names_db:
                resolved = resolve_id_from_names(canon, names_db["patient_id_map"], names_db.get("patient_alias_extra", {}))
                if resolved: final_pid = resolved
            
            p["patient_id"] = final_pid

            if final_pid in active_set:
                kept.append(p)
            else:
                moved_out_of_roster += 1
        sh["patients"] = kept

    # construir master pacientes (activos)
    patients_master: List[Dict[str, Any]] = []
    for canon in patient_roster:
        pid = stable_id("pat", canon)
        if pid not in active_set:
            continue
        aliases = set(PATIENT_ALIASES.get(canon, []))
        aliases |= patient_aliases_seen.get(canon, set())
        patients_master.append({
            "patient_id": pid,
            "canonical_name": canon,
            "aliases": sorted(a for a in aliases if a),
            "first_seen": patient_first_seen.get(pid),
            "last_seen": patient_last_seen.get(pid),
            "mentions": patient_counts.get(pid, 0),
        })
    patients_master.sort(key=lambda x: (-x["mentions"], x["canonical_name"]))

    # master nurses (solo los que postearon en el período)
    nurses_master: List[Dict[str, Any]] = []
    # map id->canon (en este override, el ID es estable por canonical)
    id_to_nurse: Dict[str, str] = {}
    for canon in nurse_roster:
        nid = stable_id("nurse", canon)
        id_to_nurse[nid] = canon
    # sumar los que no están en roster pero aparecen como sender
    for sh in shifts:
        nid = sh.get("posted_by_id")
        nname = sh.get("posted_by")
        if nid and nid not in id_to_nurse and nname:
            id_to_nurse[nid] = nname

    for nid, canon in id_to_nurse.items():
        posts = nurse_counts.get(nid, 0)
        if posts <= 0:
            continue
        aliases = set(NURSE_ALIASES.get(canon, []))
        aliases |= nurse_aliases_seen.get(canon, set())
        nurses_master.append({
            "nurse_id": nid,
            "canonical_name": canon,
            "aliases": sorted(a for a in aliases if a),
            "shift_posts": posts,
        })
    nurses_master.sort(key=lambda x: (-x["shift_posts"], x["canonical_name"]))

    # ---- Build patient-centric output ----
    # Index: pid -> list of merged shift entries for that patient
    patient_shifts: Dict[str, List[Dict[str, Any]]] = {}
    # Internal map to group within each patient: (logical_date_str, shift_label, unit) -> entry
    patient_grouped: Dict[str, Dict[Tuple[str, str, str], Dict[str, Any]]] = {}

    for sh in shifts:
        sent_at_dt = datetime.fromisoformat(sh["sent_at"])
        shift_label = sh["shift_label"]
        unit = sh["unit"]
        
        # Calcular fecha lógica del turno (Noche: 22:00-06:00)
        # Si es post-medianoche pero antes de las 06, pertenece al día anterior.
        logical_date = sent_at_dt.date()
        if shift_label == "Turno noche" and sent_at_dt.hour < 6:
            logical_date = (sent_at_dt - timedelta(days=1)).date()
        ld_str = logical_date.isoformat()

        for p in sh.get("patients", []):
            pid = p["patient_id"]
            if not pid: continue
            
            p_key = (ld_str, shift_label, unit)
            
            # Normalizar signos vitales
            v = p.get("vitals") or {}
            norm_v = {
                "ta_sis": v.get("ta_sis"),
                "ta_dia": v.get("ta_dia"),
                "sat": v.get("sat"),
                "fc": v.get("fc"),
            }
            
            note = (p.get("note") or "").strip()
            
            if pid not in patient_grouped:
                patient_grouped[pid] = {}
            
            if p_key not in patient_grouped[pid]:
                # Primer mensaje detectado para este turno/piso/paciente
                patient_grouped[pid][p_key] = {
                    "shift_id": sh["shift_id"],
                    "posted_by": sh["posted_by"],
                    "posted_by_id": sh["posted_by_id"],
                    "sent_at": sh["sent_at"],
                    "shift_label": shift_label,
                    "unit": unit,
                    "vitals": norm_v,
                    "note": note,
                    "raw_header": sh["raw_header"],
                    "summary_shift": "\n".join(sh["shift_notes"]),
                }
            else:
                # Combinar con entrada existente para evitar duplicados en el mismo turno
                existing = patient_grouped[pid][p_key]
                # Combinar vitales (priorizar valores no nulos)
                for vk in norm_v:
                    if norm_v[vk] is not None:
                        existing["vitals"][vk] = norm_v[vk]
                # Combinar notas
                if note:
                    if existing["note"]:
                        # Evitar repetir exactamente la misma nota if el mensaje se reenvió
                        if note not in existing["note"]:
                            existing["note"] += " | " + note
                    else:
                        existing["note"] = note
                
                # Combinar summary_shift
                summary = "\n".join(sh["shift_notes"])
                if summary:
                    if existing["summary_shift"]:
                        if summary not in existing["summary_shift"]:
                            existing["summary_shift"] += "\n" + summary
                    else:
                        existing["summary_shift"] = summary

    # Convertir a formato de lista final para cada paciente, ordenado cronológicamente por fecha lógica
    for pid, group_map in patient_grouped.items():
        sorted_keys = sorted(group_map.keys()) # Ordenar por (fecha, turno, unidad)
        patient_shifts[pid] = [group_map[k] for k in sorted_keys]

    # Build the patients list in the target format
    patients_out: List[Dict[str, Any]] = []

    # Map canon -> pid para resolver consistencia en la salida
    active_canon_list = sorted(list(active_canons))
    # Ordenar por frecuencia
    active_canon_list.sort(key=lambda c: (-patient_counts.get(c, 0), c))

    for canon in active_canon_list:
        final_pid = stable_id("pat", canon)
        if names_db:
            resolved = resolve_id_from_names(canon, names_db["patient_id_map"], names_db.get("patient_alias_extra", {}))
            if resolved: final_pid = resolved
        
        # Nombre display desde names-id
        display_name = canon
        if names_db:
            id_to_pat_name = {v: names_db["patient_name_map"].get(k, "") for k, v in names_db["patient_id_map"].items()}
            display_name = id_to_pat_name.get(final_pid, canon)

        # Nickname
        all_aliases = sorted(patient_aliases_seen.get(canon, set()) | set(PATIENT_ALIASES.get(canon, [])))
        nickname = next((a for a in all_aliases if a and a != canon), display_name)

        # Recuperar sus shifts del mapa recolectado
        # Nota: patient_shifts se llenó usando el f'real_pid' o el 'pid' original
        # En la lógica actual sh['patients'] ya tiene el pid final correcto
        
        data_shifts = patient_shifts.get(final_pid, [])

        patients_out.append({
            "patient_id": final_pid,
            "patient": display_name,
            "nickname": nickname,
            "shifts": data_shifts,
        })


    data = {"patients": patients_out}
    
    # Agregar metadatos de calidad
    data["quality_stats"] = {
        "date_start": msgs[0].dt.isoformat(),
        "date_end": msgs[-1].dt.isoformat(),
        "ignored_lines": filtered_as_note + filtered_as_nurse,
        "unmatched_patients": unmatched_patient_lines,
        "vitals_captured": total_vitals_count,
        "total_lines": total_lines_processed,
        "nurses_active": len(nurses_master)
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return data


# ----------------------------
# CLI
# ----------------------------

def main():
    ap = argparse.ArgumentParser(description="Parser de partes de WhatsApp para RGP")
    ap.add_argument("input_txt", help="Archivo .txt exportado del chat de WhatsApp")
    ap.add_argument("output_json", help="Archivo .json de salida")
    ap.add_argument("--active_patients", type=int, default=80,
                    help="Máximo de pacientes activos en el padrón (default: 80)")
    ap.add_argument("--expected_nurses", type=int, default=50,
                    help="Cantidad aproximada de enfermeros (default: 50)")
    ap.add_argument("--names_id", default=None,
                    help="Ruta a names-id.txt con IDs reales de pacientes y enfermeros")
    args = ap.parse_args()

    data = build_dataset(
        txt_path=args.input_txt,
        output_path=args.output_json,
        expected_active_patients=args.active_patients,
        expected_nurses_approx=args.expected_nurses,
        names_id_path=args.names_id,
    )

    print("OK")
    patients_count = len(data["patients"])
    total_shifts = sum(len(p["shifts"]) for p in data["patients"])
    qs = data.get("quality_stats", {})
    
    # Imprimir estadísticas para captura externa
    import json as json_stats
    stats_out = {
        "patients": patients_count,
        "total_shifts": total_shifts,
        "date_range": [qs.get("date_start"), qs.get("date_end")],
        "ignored_lines": qs.get("ignored_lines"),
        "vitals_captured": qs.get("vitals_captured"),
        "nurses_active": qs.get("nurses_active")
    }
    print(f"JSON_STATS:{json_stats.dumps(stats_out)}")

if __name__ == "__main__":
    main()
