import json
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime

def run_parser(script_name, input_txt, output_json, names_id):
    print(f"\n--- Ejecutando {script_name} ---")
    if not os.path.exists(input_txt):
        print(f"Error: No se encuentra el archivo de entrada {input_txt}")
        return None
    
    start_time = time.time()
    file_size = os.path.getsize(input_txt) / 1024  # KB
        
    cmd = [sys.executable, script_name, input_txt, output_json]
    if names_id and os.path.exists(names_id):
        cmd.extend(["--names_id", names_id])
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    execution_time = time.time() - start_time
    
    # Mostrar la salida original para que el usuario la vea
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    if result.returncode != 0:
        print(f"Error al ejecutar {script_name}")
        return None
    
    # Buscar estadísticas en la salida
    stats = {
        "execution_time_sec": round(execution_time, 2),
        "input_file_size_kb": round(file_size, 2)
    }
    for line in result.stdout.splitlines():
        if line.startswith("JSON_STATS:"):
            try:
                stats.update(json.loads(line.replace("JSON_STATS:", "")))
                break
            except Exception:
                pass
    return stats

def join_jsons(enfermeria_path, pharmacy_path, output_path):
    print(f"\n--- Iniciando proceso de JOIN ---")
    print(f"Leyendo enfermería desde: {enfermeria_path}")
    if not os.path.exists(enfermeria_path):
        print(f"Error: No se encuentra {enfermeria_path}")
        return None
        
    with open(enfermeria_path, 'r', encoding='utf-8') as f:
        enfermeria_data = json.load(f)
    
    # Enfermería suele venir como {"patients": [...]}
    if isinstance(enfermeria_data, dict) and "patients" in enfermeria_data:
        enfermeria_list = enfermeria_data["patients"]
    elif isinstance(enfermeria_data, list):
        enfermeria_list = enfermeria_data
    else:
        print("Error: El formato de enfermería no es reconocido.")
        return None

    print(f"Leyendo farmacia desde: {pharmacy_path}")
    if not os.path.exists(pharmacy_path):
        print(f"Error: No se encuentra {pharmacy_path}")
        return None

    with open(pharmacy_path, 'r', encoding='utf-8') as f:
        pharmacy_data = json.load(f)
    
    # Farmacia suele venir como una lista [...]
    if isinstance(pharmacy_data, dict) and "patients" in pharmacy_data:
        pharmacy_list = pharmacy_data["patients"]
    elif isinstance(pharmacy_data, list):
        pharmacy_list = pharmacy_data
    else:
        print("Error: El formato de farmacia no es reconocido.")
        return None

    # Usamos un diccionario para indexar por patient_id
    combined = {}

    # Procesar enfermería
    for p in enfermeria_list:
        pid = p.get("patient_id")
        if not pid:
            continue
        combined[pid] = {
            "patient_id": pid,
            "patient": p.get("patient"),
            "nickname": p.get("nickname", ""),
            "shifts": p.get("shifts", []),
            "shiftsPharmacy": []
        }

    # Procesar farmacia (unir con lo anterior)
    for p in pharmacy_list:
        pid = p.get("patient_id")
        if not pid:
            continue
        
        if pid in combined:
            combined[pid]["shiftsPharmacy"] = p.get("shiftsPharmacy", [])
            # Actualizar nombre/nickname si están vacíos en el primero pero no en el segundo
            if not combined[pid]["patient"] and p.get("patient"):
                combined[pid]["patient"] = p["patient"]
            if not combined[pid]["nickname"] and p.get("nickname"):
                combined[pid]["nickname"] = p["nickname"]
        else:
            # Si no estaba en enfermería, lo agregamos
            combined[pid] = {
                "patient_id": pid,
                "patient": p.get("patient"),
                "nickname": p.get("nickname", ""),
                "shifts": [],
                "shiftsPharmacy": p.get("shiftsPharmacy", [])
            }

    # Convertir de vuelta a lista y ordenar por nombre
    final_list = sorted(combined.values(), key=lambda x: (x.get("patient") or "").lower())
    
    result = {"patients": final_list}

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Éxito: Se han unido {len(final_list)} pacientes en '{output_path}'")

    # Calcular estadísticas finales
    stats = {
        "timestamp": datetime.now().isoformat(),
        "total_patients": len(final_list)
    }
    return stats

def save_execution_history(history_file, stats, inputs, parser_stats):
    print(f"Guardando historial en: {history_file}")
    history = []
    if os.path.exists(history_file):
        try:
            with open(history_file, 'r', encoding='utf-8') as f:
                history = json.load(f)
        except Exception:
            history = []
    
    entry = {
        "stats": stats,
        "parser_stats": parser_stats,
        "inputs": inputs
    }
    history.append(entry)
    
    with open(history_file, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def main():
    parser = argparse.ArgumentParser(description="Parser Unificado: Ejecuta parsers y une los JSON.")
    
    # Argumentos para los inputs (TXTs)
    parser.add_argument("--txt_enfermeria", 
                        default="Chat de WhatsApp con Grupo asistentes 11-2025.txt",
                        help="Archivo TXT de enfermería")
    parser.add_argument("--txt_indicaciones", 
                        default="Chat de WhatsApp con Farmacia. Indic médicas11-2025.txt",
                        help="Archivo TXT de indicaciones médicas")
    
    # Argumentos para los JSONs intermedios
    parser.add_argument("--json_enfermeria", 
                        default="resultado_enfemeria.json",
                        help="JSON intermedio de enfermería")
    parser.add_argument("--json_farmacia", 
                        default="resultado_pharmacy.json",
                        help="JSON intermedio de farmacia")
    
    # Argumentos comunes
    parser.add_argument("--names_id", default="names-id.txt", help="Ruta a names-id.txt")
    parser.add_argument("--output", default="residentes_db.json", 
                        help="Nombre del archivo final (por defecto: residentes_db.json)")
    parser.add_argument("--history_file", default="history_parser.json",
                        help="Archivo para guardar el historial de ejecuciones")

    
    args = parser.parse_args()
    
    # 1. Ejecutar json_parser_enfemeria.py
    stats_enf = run_parser("json_parser_enfemeria.py", args.txt_enfermeria, args.json_enfermeria, args.names_id)
    
    # 2. Ejecutar json_parser_indicaciones.py
    stats_ind = run_parser("json_parser_indicaciones.py", args.txt_indicaciones, args.json_farmacia, args.names_id)
    
    if stats_enf is not None and stats_ind is not None:
        # 3. Unir y obtener estadísticas finales
        final_stats = join_jsons(args.json_enfermeria, args.json_farmacia, args.output)
        
        if final_stats:
            # 4. Guardar historial
            inputs = {
                "enfermeria_input": args.txt_enfermeria,
                "indicaciones_input": args.txt_indicaciones,
                "names_id": args.names_id
            }
            parser_stats = {
                "enfermeria": stats_enf,
                "farmacia": stats_ind
            }
            save_execution_history(args.history_file, final_stats, inputs, parser_stats)
    else:
        print("\nOmitiendo el JOIN debido a errores en los parsers.")

if __name__ == "__main__":
    main()



