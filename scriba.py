# Scriba by Gabriele Battaglia (IZ4APU)
# Data concepimento mercoledì 21 novembre 2025.
# TODO:
# - Valutare suddivisione in moduli (engine.py, ui.py, settings.py)
# - Implementare rotazione log con timestamp invece di sovrascrittura
# - Aggiungere unit test per le funzioni di utility (fix_long_path, format_size)

import os
import json
import re
import copy
import subprocess
import datetime
import time
import platform
import shutil
from typing import Optional
import tkinter as tk
from tkinter import filedialog

# --- CONFIGURAZIONE E COSTANTI ---
APP_NAME = "Scriba"
APP_VERSION = "2.5.1 di maggio 2026"
SETTINGS_FILE = "scriba_settings.json"
# ... (rest of constants remains same)
REFRESH_RATE = 3.0
PRESET_TEMPLATE = {
    "titolo": "Casual",
    "machine_id": "God's Machine",
    "giorni_periodicita": 365,
    "ultimo_backup": None,
    "root_destinazione": "",
    "coppie_cartelle": [],
    "esclusioni": [],
    "storico_stats": {}
}

# --- GESTIONE DATI E SICUREZZA ---

def get_machine_id() -> str:
    hostname = platform.node()
    try:
        username = os.getlogin()
    except Exception:
        username = os.environ.get('USERNAME', 'Unknown')
    return f"{hostname} | {username}"

def load_settings() -> Optional[dict]:
    if not os.path.exists(SETTINGS_FILE):
        return {"presets": []}
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"ERRORE CRITICO caricamento settings: {e}")
        return None 

def save_settings(data: Optional[dict]) -> None:
    if data is None: return
    if os.path.exists(SETTINGS_FILE):
        try:
            shutil.copy2(SETTINGS_FILE, SETTINGS_FILE + ".bak")
        except Exception: pass
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"ERRORE SALVATAGGIO: {e}")

def fix_long_path(path: str) -> str:
    if os.name == 'nt' and len(path) > 0 and not path.startswith('\\\\?\\'):
        path = os.path.abspath(path)
        if path.startswith('\\\\'):
            return '\\\\?\\UNC\\' + path[2:] 
        return '\\\\?\\' + path 
    return path

# --- INTERFACCIA UTENTE E UTILITIES ---

def get_folder_dialog(message: str = "Seleziona una cartella") -> Optional[str]:
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    folder_path = filedialog.askdirectory(title=message)
    root.destroy()
    return folder_path if folder_path else None

def smart_truncate(text: str, max_len: int = 45) -> str:
    if len(text) <= max_len:
        return text
    part_len = (max_len - 3) // 2
    head = text[:part_len]
    tail = text[-part_len:]
    return f"{head}...{tail}"

def format_size(bytes_val: float) -> str:
    sign = ""
    if bytes_val < 0:
        sign = "-"
        bytes_val = abs(bytes_val)
    if bytes_val == 0: return "0.00 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_val < 1024.0:
            return f"{sign}{bytes_val:.2f} {unit}"
        bytes_val /= 1024.0
    return f"{sign}{bytes_val:.2f} PB"

def stampa_dettaglio_esteso(preset: dict) -> None:
    print("\n" + "="*60)
    print(f"RIEPILOGO PRESET: {preset['titolo']}")
    print("="*60)
    print(f"ID Macchina:       {preset.get('machine_id', 'N/A')}")
    print(f"Periodicità:       {preset['giorni_periodicita']} giorni")
    print(f"Ultima Esecuzione: {preset['ultimo_backup'] or 'Mai'}")
    print(f"Root Destinazione: {preset['root_destinazione']}")
    print("-" * 60)
    num_cartelle = len(preset['coppie_cartelle'])
    print(f"Cartelle da elaborare ({num_cartelle}):")
    if num_cartelle > 15:
        # Mostriamo solo le prime 5 e le ultime 5
        for c in preset['coppie_cartelle'][:5]:
            print(f"  [SRC] {c['origine']}")
        print(f"  ... [saltate {num_cartelle - 10} cartelle nel mezzo per brevità] ...")
        for c in preset['coppie_cartelle'][-5:]:
            print(f"  [SRC] {c['origine']}")
    else:
        for c in preset['coppie_cartelle']:
            print(f"  [SRC] {c['origine']}")
            print(f"  [DST] ...\\{c['nome_cartella']}")
    print("="*60 + "\n")

def cleanup_old_logs(log_dir: str, max_age_days: int = 30) -> None:
    """
    Rimuove i file di log più vecchi di max_age_days dalla cartella dei log.
    """
    if not os.path.exists(log_dir):
        return
    now = time.time()
    limit = now - (max_age_days * 86400)
    try:
        for filename in os.listdir(log_dir):
            if filename.endswith(".txt"):
                filepath = os.path.join(log_dir, filename)
                if os.path.isfile(filepath):
                    file_mtime = os.path.getmtime(filepath)
                    if file_mtime < limit:
                        try:
                            os.remove(filepath)
                        except Exception:
                            pass
    except Exception:
        pass

def play_completion_sound(success: bool = True) -> None:
    """
    Riproduce un segnale acustico: un tono lungo per successo,
    tre toni brevi e consecutivi per segnalare errori.
    """
    try:
        import winsound
        if success:
            winsound.Beep(1000, 500)
        else:
            for _ in range(3):
                winsound.Beep(500, 150)
                time.sleep(0.1)
    except Exception:
        print("\a", end="", flush=True)

# --- LOGICA DI UTILITY PER I COMANDI E PARSING ROBOCOPY ---

def build_robocopy_cmd(src: str, dst: str, user_exclusions: Optional[list[str]] = None, is_simulation: bool = False, is_plan: bool = False) -> list[str]:
    """
    Costruisce il comando Robocopy in modo standardizzato, unificando le esclusioni
    ed evitando bug di posizionamento dei parametri.
    """
    cmd_src = src.replace("\\\\?\\UNC\\", "\\\\").replace("\\\\?\\", "")
    if cmd_src.endswith("\\") and not cmd_src.endswith(":\\"):
        cmd_src = cmd_src.rstrip("\\")
        
    cmd_dst = dst.replace("\\\\?\\UNC\\", "\\\\").replace("\\\\?\\", "")
    if cmd_dst.endswith("\\") and not cmd_dst.endswith(":\\"):
        cmd_dst = cmd_dst.rstrip("\\")

    if is_plan:
        cmd = ["robocopy", cmd_src, cmd_dst, "/MIR", "/XJ", "/R:1", "/W:1", "/L", "/BYTES", "/NJH", "/NJS", "/NDL", "/NC"]
    else:
        cmd = ["robocopy", cmd_src, cmd_dst, "/MIR", "/XJ", "/R:1", "/W:1", "/FFT", "/NDL", "/NP", "/BYTES"]
        if is_simulation:
            cmd.append("/L")
            
    # Esclusioni directory di sistema / recycle
    xd_dirs = ["$RECYCLE.BIN", "System Volume Information"]
    
    # Esclusioni ROOT-ONLY (Recovery)
    drive, tail = os.path.splitdrive(src)
    if tail in ['\\', '/', ''] or src.endswith(':\\'):
        xd_dirs.append("Recovery")
        
    # Esclusioni utente
    if user_exclusions:
        clean_excl = [e.replace("\\\\?\\UNC\\", "\\\\").replace("\\\\?\\", "") for e in user_exclusions]
        xd_dirs.extend(clean_excl)
        
    cmd.extend(["/XD"] + xd_dirs)
    cmd.extend(["/XF", "pagefile.sys", "hiberfil.sys", "swapfile.sys"])
    
    return cmd

def parse_robocopy_error(line: str) -> Optional[dict]:
    """
    Esegue il parsing di una riga di robocopy contenente un errore e ne estrae i dettagli.
    """
    match = re.search(r"ERROR\s+(\d+)\s+\((0x[0-9a-fA-F]+)\)(?:\s+(.*))?", line)
    if match:
        err_num = match.group(1)
        err_hex = match.group(2)
        action_path = match.group(3) or "Dettagli non disponibili"
        return {
            "code_dec": int(err_num),
            "code_hex": err_hex,
            "detail": action_path.strip()
        }
    return None

def print_progress_line(current_task_name: str, current_bytes_global: int, total_bytes_global: int, start_time_global: float) -> None:
    """
    Stampa una riga di avanzamento adatta a screen reader e CLI, aggiornandola sul posto.
    """
    elapsed_time = time.time() - start_time_global
    speed_val = current_bytes_global / elapsed_time if elapsed_time > 0 else 0
    speed_str = f"{format_size(speed_val)}/s"
    
    if total_bytes_global > 0:
        percent = min(100.0, (current_bytes_global / total_bytes_global) * 100)
        remaining_bytes = max(0, total_bytes_global - current_bytes_global)
        eta_seconds = remaining_bytes / speed_val if speed_val > 0 else 0
        
        if eta_seconds > 0:
            m_eta, s_eta = divmod(int(eta_seconds), 60)
            h_eta, m_eta = divmod(m_eta, 60)
            if h_eta > 0:
                eta_str = f"{h_eta:02d}:{m_eta:02d}:{s_eta:02d}"
            else:
                eta_str = f"{m_eta:02d}:{s_eta:02d}"
        else:
            eta_str = "--:--"
            
        bar_len = 20
        filled_len = int(round(bar_len * percent / 100))
        bar = "#" * filled_len + "-" * (bar_len - filled_len)
        
        progress_text = f"\r   --> {current_task_name} | [{bar}] {percent:.1f}% ({format_size(current_bytes_global)}/{format_size(total_bytes_global)}) | {speed_str} | ETA: {eta_str}"
    else:
        m_el, s_el = divmod(int(elapsed_time), 60)
        h_el, m_el = divmod(m_el, 60)
        time_str = f"{h_el:02d}:{m_el:02d}:{s_el:02d}" if h_el > 0 else f"{m_el:02d}:{s_el:02d}"
        progress_text = f"\r   --> {current_task_name} | Elaborati: {format_size(current_bytes_global)} | {speed_str} | Tempo: {time_str}"
        
    print(progress_text.ljust(99)[:99], end="", flush=True)

def get_robocopy_plan(src: str, dst: str, user_exclusions: Optional[list[str]] = None) -> tuple[int, int]:
    """
    Esegue una simulazione rapida (/L) per ottenere:
    1. Numero di operazioni previste (files da copiare).
    2. Byte totali da trasferire (per calcolo ETA preciso).
    """
    cmd = build_robocopy_cmd(src, dst, user_exclusions, is_simulation=True, is_plan=True)
    
    files_to_copy = 0
    bytes_to_copy = 0

    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, encoding='cp850', errors='replace',
            startupinfo=startupinfo
        )
        
        for line in process.stdout:
            if line and line.strip():
                parts = line.split(maxsplit=1)
                if parts and parts[0].isdigit():
                    size = int(parts[0])
                    bytes_to_copy += size
                    files_to_copy += 1
        process.wait()
    except Exception: 
        return 0, 0
    return files_to_copy, bytes_to_copy

def run_robocopy_engine(src: str, dst: str, log_file: str, user_exclusions: Optional[list[str]] = None, is_simulation: bool = False, 
                        total_bytes_global: int = 0, current_bytes_global: int = 0, start_time_global: float = 0.0, current_task_name: str = "") -> tuple[dict[str, int], int, list[dict]]:
    """
    Esegue Robocopy in modo sincrono e pulito.
    Scrive il log e restituisce le statistiche finali.
    """
    cmd = build_robocopy_cmd(src, dst, user_exclusions, is_simulation, is_plan=False)
    
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW 
    
    final_stats = {
        "dirs_total": 0, "dirs_copied": 0, "dirs_skipped": 0, "dirs_failed": 0,
        "files_total": 0, "files_copied": 0, "files_skipped": 0, "files_failed": 0,
        "bytes_total": 0, "bytes_copied": 0, "bytes_skipped": 0, "bytes_failed": 0
    }
    
    print_progress_line(current_task_name, current_bytes_global, total_bytes_global, start_time_global)

    errori_task = []
    bytes_fatti_task = 0
    last_update_time = 0
    summary_started = False

    try:
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, encoding='cp850', errors='replace',
            startupinfo=startupinfo
        )
        
        summary_lines = []

        with open(log_file, 'w', encoding='utf-8') as f_log:
            f_log.write(f"--- AVVIO: {datetime.datetime.now()} ---\nSRC: {src}\nDST: {dst}\n\n")
            for line in process.stdout:
                f_log.write(line)
                
                if "-----------" in line:
                    summary_started = True
                
                if summary_started:
                    summary_lines.append(line)
                    continue
                
                # Check errori
                if "ERROR" in line:
                    err_info = parse_robocopy_error(line)
                    if err_info:
                        errori_task.append(err_info)
                
                # Check progresso file (deve contenere un backslash e non essere metadato)
                elif "\\" in line and not any(h in line for h in ["Source :", "Dest :", "Options :", "Started :", "Monitor :"]):
                    tokens = line.split()
                    file_size = None
                    for t in tokens:
                        if t.isdigit():
                            file_size = int(t)
                            break
                    if file_size is not None:
                        bytes_fatti_task += file_size
                        
                        # Aggiornamento temporizzato a schermo
                        now_time = time.time()
                        if now_time - last_update_time >= REFRESH_RATE:
                            temp_global = current_bytes_global + bytes_fatti_task
                            print_progress_line(current_task_name, temp_global, total_bytes_global, start_time_global)
                            last_update_time = now_time

        process.wait()

        # Parsing statistiche finali
        for l in summary_lines:
            l_low = l.lower()
            if ":" not in l: continue
            nums = [int(x) for x in l.replace(":", " ").split() if x.isdigit()]
            if len(nums) < 6: continue
            
            if "dir" in l_low or "cartell" in l_low:
                final_stats["dirs_total"] = nums[0]; final_stats["dirs_copied"] = nums[1]
                final_stats["dirs_skipped"] = nums[2]; final_stats["dirs_failed"] = nums[4]
            elif "file" in l_low:
                final_stats["files_total"] = nums[0]; final_stats["files_copied"] = nums[1]
                final_stats["files_skipped"] = nums[2]; final_stats["files_failed"] = nums[4]
            elif "byte" in l_low:
                final_stats["bytes_total"] = nums[0]; final_stats["bytes_copied"] = nums[1]
                final_stats["bytes_skipped"] = nums[2]; final_stats["bytes_failed"] = nums[4]

        # Stampa progresso finale del task
        temp_global = current_bytes_global + bytes_fatti_task
        print_progress_line(current_task_name, temp_global, total_bytes_global, start_time_global)

        return final_stats, bytes_fatti_task, errori_task

    except Exception as e:
        print(f"\nErrore Robocopy: {e}")
        return final_stats, 0, errori_task

def esegui_backup(preset_index: Optional[int] = None, simulazione: bool = False) -> None:
    settings = load_settings()
    if settings is None: return 
    
    presets = settings["presets"]
    current_machine = get_machine_id()
    
    if preset_index is None:
        print("\nQuale preset vuoi eseguire?")
        for i, p in enumerate(presets):
            mod_sim = " [SIMULAZIONE]" if simulazione else ""
            print(f"{i + 1}. {p['titolo']}{mod_sim}")
        try:
            sel = int(input("Scelta (0 per annullare): ")) - 1
            if sel == -1: return
            preset = presets[sel]
            preset_index = sel
        except (ValueError, IndexError): return
    else:
        preset = presets[preset_index]

    stampa_dettaglio_esteso(preset)
    tipo_run = "SIMULAZIONE" if simulazione else "BACKUP REALE"
    print(f"Stai per lanciare: {tipo_run}")
    if input("Vuoi procedere? (s/n): ").lower() != 's': return

    preset_machine = preset.get("machine_id", "Sconosciuto")
    if preset_machine != current_machine and not simulazione:
        print(f"\nATTENZIONE: ID Macchina non corrispondente ({preset_machine}).")
        if input("Scrivi 'SI' per forzare: ") != "SI": return

    # --- CONTROLLO RAGGIUNGIBILITA DESTINAZIONE ---
    root_dest = preset["root_destinazione"]
    dest_ok = False
    temp_path = root_dest
    while temp_path:
        if os.path.exists(temp_path):
            dest_ok = True
            break
        parent = os.path.dirname(temp_path)
        if parent == temp_path:
            break
        temp_path = parent
        
    if not dest_ok:
        print("\nERRORE CRITICO: La destinazione del backup non è raggiungibile o non esiste:")
        print(f"   {root_dest}")
        print("Verificare la connessione di rete o che il disco sia montato correttamente.")
        input("\nPremi INVIO per tornare al menu...")
        return

    # --- CONTROLLO ESISTENZA ORIGINI ---
    cartelle_valide = []
    for c in preset["coppie_cartelle"]:
        src = fix_long_path(c["origine"])
        if os.path.exists(src):
            cartelle_valide.append(c)
        else:
            print(f"AVVISO: Origine non trovata, verrà saltata: {c['origine']}")
    
    if not cartelle_valide:
        print("Nessuna cartella valida da copiare.")
        input("\nPremi INVIO per tornare al menu...")
        return

    if not simulazione and preset["ultimo_backup"]:
        try:
            d = datetime.datetime.strptime(preset["ultimo_backup"], "%Y-%m-%d").date()
            if (datetime.date.today() - d).days < preset["giorni_periodicita"]:
                print("AVVISO: Periodicità non ancora scaduta.")
                if input("Procedere comunque? (s/n): ").lower() != 's': return
        except Exception: pass

    spegni_pc = False
    if not simulazione:
        spegni_pc = (input("\nVuoi spegnere il PC al termine? (s/n): ").lower() == 's')

    start_total = time.time()
    log_dir = os.path.join(root_dest, "Logs")
    if not os.path.exists(log_dir):
        try: os.makedirs(log_dir)
        except Exception: pass 
    cleanup_old_logs(log_dir, max_age_days=30) 

    # --- STIMA DELLE DIMENSIONI INIZIALI (ETA) ---
    total_bytes_global = 0
    prev_data = preset.get("storico_stats", {}).get(current_machine, {})
    total_bytes_global = prev_data.get("total_bytes", 0)

    if total_bytes_global == 0:
        print("\nCalcolo dimensioni iniziali in corso (richiesto solo al primo avvio)...")
        for coppia in cartelle_valide:
            src = fix_long_path(coppia["origine"])
            dst = fix_long_path(os.path.join(root_dest, coppia["nome_cartella"]))
            files_cnt, bytes_cnt = get_robocopy_plan(src, dst, user_exclusions=preset.get("esclusioni", []))
            total_bytes_global += bytes_cnt
        print(f"Dimensione totale stimata: {format_size(total_bytes_global)}")

    # --- ESECUZIONE ---
    print(f"\n--- Esecuzione {tipo_run} ---")
    
    current_bytes_global = 0
    start_time_global = time.time()
    lista_errori = []
    
    report_files_copied = 0
    report_bytes_copied = 0
    report_files_failed = 0
    report_files_skipped = 0
    report_bytes_skipped = 0
    
    snapshot_files = 0
    snapshot_bytes = 0
    
    dettaglio_sessione = []

    for i, coppia in enumerate(cartelle_valide):
        src = fix_long_path(coppia["origine"])
        dst = fix_long_path(os.path.join(root_dest, coppia["nome_cartella"]))
        nome_dir = coppia["nome_cartella"]
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
        log_file = os.path.join(log_dir, f"{nome_dir}-{timestamp}.txt")
        
        task_start_time = time.time()

        stats, bytes_fatti, errori_task = run_robocopy_engine(
            src, dst, log_file,
            user_exclusions=preset.get("esclusioni", []),
            is_simulation=simulazione,
            total_bytes_global=total_bytes_global,
            current_bytes_global=current_bytes_global,
            start_time_global=start_time_global,
            current_task_name=nome_dir
        )
        
        current_bytes_global += bytes_fatti
        lista_errori.extend(errori_task)
        
        report_files_copied += stats.get("files_copied", 0)
        report_files_failed += stats.get("files_failed", 0)
        report_files_skipped += stats.get("files_skipped", 0)
        report_bytes_copied += stats.get("bytes_copied", 0)
        report_bytes_skipped += stats.get("bytes_skipped", 0)
        snapshot_files += stats.get("files_total", 0)
        snapshot_bytes += stats.get("bytes_total", 0)

        task_duration = time.time() - task_start_time
        
        dettaglio_sessione.append({
            "nome": nome_dir,
            "origine": coppia["origine"],
            "files_copied": stats.get("files_copied", 0),
            "bytes_copied": stats.get("bytes_copied", 0),
            "files_skipped": stats.get("files_skipped", 0),
            "files_failed": stats.get("files_failed", 0),
            "files_total": stats.get("files_total", 0),
            "bytes_total": stats.get("bytes_total", 0),
            "duration": task_duration
        })

        m_task, s_task = divmod(int(task_duration), 60)
        status_msg = f"   [OK] {nome_dir} ({i+1}/{len(cartelle_valide)}) - Tempo: {m_task:02d}:{s_task:02d}"
        print(f"\r{status_msg.ljust(99)[:99]}")

    play_completion_sound(success=(report_files_failed == 0))
    print("\n" + "="*60) 

    # ============================================================
    # REPORT FINALE (COMPARATIVO E DETTAGLIATO)
    # ============================================================
    
    # Gestione Storico Stats
    if "storico_stats" not in preset: preset["storico_stats"] = {}
    
    # Recupero dati precedenti
    prev_data = preset["storico_stats"].get(current_machine, {})
    prev_files = prev_data.get("total_files", 0)
    prev_bytes = prev_data.get("total_bytes", 0)
    last_run_date = prev_data.get("last_run_date", "Mai")

    # Aggiornamento dati (solo se non simulazione)
    if not simulazione and settings:
        preset["ultimo_backup"] = datetime.date.today().strftime("%Y-%m-%d")
        preset["storico_stats"][current_machine] = {
            "last_run_date": datetime.date.today().strftime("%Y-%m-%d"),
            "total_files": snapshot_files,
            "total_bytes": snapshot_bytes
        }
        save_settings(settings)
    
    total_time = time.time() - start_total
    m_tot, s_tot = divmod(total_time, 60)
    h_tot, m_tot = divmod(m_tot, 60)
    
    print(f"\nRIEPILOGO SESSIONE - {tipo_run}")
    print("="*60)
    print(f"Tempo Totale:     {int(h_tot):02d}:{int(m_tot):02d}:{s_tot:06.3f}")
    
    # Velocità Media Reale
    speed_str = "0.00 B/s"
    if total_time > 0 and report_bytes_copied > 0:
        speed_val = report_bytes_copied / total_time
        speed_str = f"{format_size(speed_val)}/s"

    print("-" * 60)
    print(f"{'STATISTICHE OPERAZIONI (Sessione Corrente)':<50}")
    print("-" * 60)
    
    print(f"File Copiati:    {str(report_files_copied):<10} ({format_size(report_bytes_copied)})")
    print(f"File Invariati:  {str(report_files_skipped):<10} (Saltati)")
    print(f"File Falliti:    {str(report_files_failed):<10}")
    print(f"Velocità Media:  {speed_str}")

    # Rapporto efficienza
    tot_files_processed = report_files_copied + report_files_skipped
    if tot_files_processed > 0:
        efficienza = (report_files_skipped / tot_files_processed) * 100
        print(f"Efficienza:      {efficienza:.2f}% (File invariati non riscritti)")

    prossimo = datetime.date.today() + datetime.timedelta(days=preset["giorni_periodicita"])
    print(f"Prossimo Backup: {prossimo.strftime('%Y-%m-%d')} (ogni {preset['giorni_periodicita']} giorni)")

    # Tabella per-cartella
    print("-" * 60)
    print("DETTAGLIO PER CARTENLLA")
    print("-" * 60)
    print(f"{'NOME':<20} {'COPIATI':<10} {'DIM. COPIATA':<15} {'TEMPO':<8} {'VEL. MEDIA'}")
    print("-" * 60)
    for det in dettaglio_sessione:
        n = smart_truncate(det["nome"], 18)
        fc = det["files_copied"]
        bc = format_size(det["bytes_copied"])
        
        m_t, s_t = divmod(int(det["duration"]), 60)
        t_str = f"{m_t:02d}:{s_t:02d}"
        
        v_val = det["bytes_copied"] / det["duration"] if det["duration"] > 0 else 0
        v_str = f"{format_size(v_val)}/s" if det["bytes_copied"] > 0 else "0.00 B/s"
        
        print(f"{n:<20} {fc:<10} {bc:<15} {t_str:<8} {v_str}")
    print("-" * 60)

    # Top 5 dimensione
    top_dim = sorted([d for d in dettaglio_sessione if d["bytes_copied"] > 0], key=lambda x: x["bytes_copied"], reverse=True)[:5]
    if top_dim:
        print("TOP 5 CARTELLE PER DATI TRASFERITI")
        print("-" * 60)
        for idx, d in enumerate(top_dim):
            print(f" {idx+1}. {d['nome']}: {format_size(d['bytes_copied'])} in {d['files_copied']} file")
        print("-" * 60)

    # Top 5 tempo
    top_tempo = sorted(dettaglio_sessione, key=lambda x: x["duration"], reverse=True)[:5]
    if top_tempo:
        print("TOP 5 CARTELLE PIÙ LENTE (PER TEMPO)")
        print("-" * 60)
        for idx, d in enumerate(top_tempo):
            m_t, s_t = divmod(int(d["duration"]), 60)
            print(f" {idx+1}. {d['nome']}: {m_t:02d}:{s_t:02d} ({format_size(d['bytes_copied'])} trasferiti)")
        print("-" * 60)

    print(f"{'CONFRONTO STORICO ARCHIVIO (vs ' + last_run_date + ')':<50}")
    print("-" * 60)

    if prev_bytes == 0 and prev_files == 0:
        print(" Nessun dato storico disponibile per il confronto.")
        print(f" Stato Attuale:   {format_size(snapshot_bytes)} in {snapshot_files} files.")
    else:
        diff_bytes = snapshot_bytes - prev_bytes
        diff_files = snapshot_files - prev_files
        
        perc_bytes = 0.0
        if prev_bytes > 0: perc_bytes = (diff_bytes / prev_bytes) * 100
        
        perc_files = 0.0
        if prev_files > 0: perc_files = (diff_files / prev_files) * 100

        sign_b = "+" if diff_bytes >= 0 else ""
        sign_f = "+" if diff_files >= 0 else ""
        
        print(f"{'METRICA':<15} {'PRECEDENTE':<15} {'ATTUALE':<15} {'VARIAZIONE'}")
        print("-" * 60)
        print(f"{'Dimensioni':<15} {format_size(prev_bytes):<15} {format_size(snapshot_bytes):<15} {sign_b}{format_size(diff_bytes)} ({sign_b}{perc_bytes:.2f}%)")
        print(f"{'Numero File':<15} {str(prev_files):<15} {str(snapshot_files):<15} {sign_f}{diff_files} ({sign_f}{perc_files:.2f}%)")

    # Errori inline se <= 10
    if report_files_failed > 0:
        print("\n" + "!"*60)
        print(f" ATTENZIONE: {report_files_failed} operazioni non sono andate a buon fine.")
        if len(lista_errori) <= 10 and lista_errori:
            print(" Dettaglio errori:")
            for err in lista_errori:
                print(f"   - [ERR {err['code_hex']}] {err['detail']}")
        else:
            print(" CONTROLLARE I LOG NELLA CARTELLA DI DESTINAZIONE PER I DETTAGLI!")
        print("!"*60)

    print("="*60)

    if spegni_pc:
        print("\nSpegnimento tra 60s. CTRL+C per annullare.")
        try:
            os.system("shutdown /s /t 60")
            time.sleep(60) 
        except KeyboardInterrupt:
            os.system("shutdown /a")
            print("\nSpegnimento annullato.")
    else:
        if os.path.exists(log_dir):
            print(f"\nLogs salvati in: {log_dir}")
        input("\nPremi INVIO per tornare al menu...")
# --- FUNZIONI DI MENU ---

def crea_nuovo_preset():
    print(f"--- {APP_NAME} | Crea Nuovo Preset ---\n")
    titolo = input("Titolo backup: ").strip()
    if not titolo: return

    try:
        giorni = int(input("Periodicità (giorni): "))
    except ValueError: return

    print("\nDestinazione Root...")
    root_dest = get_folder_dialog(f"Destinazione per '{titolo}'")
    if not root_dest: return

    nuovo_preset = copy.deepcopy(PRESET_TEMPLATE)
    nuovo_preset["titolo"] = titolo
    nuovo_preset["machine_id"] = get_machine_id()
    nuovo_preset["giorni_periodicita"] = giorni
    nuovo_preset["root_destinazione"] = root_dest
    nuovo_preset["coppie_cartelle"] = []

    while True:
        print(f"\nOrigini inserite: {len(nuovo_preset['coppie_cartelle'])}")
        if input("Aggiungere origine? (s/n): ").lower() != 's': break
        path = get_folder_dialog("Nuova Origine")
        if path:
            nome = os.path.basename(path) or path.replace(":", "").replace("\\", "")
            nuovo_preset["coppie_cartelle"].append({"origine": path, "nome_cartella": nome})
            print(f"OK: {nome}")
    # --- NUOVO BLOCCO PER LE ESCLUSIONI ---
    nuovo_preset["esclusioni"] = [] # Inizializziamo la lista vuota
    while True:
        print(f"\nEsclusioni inserite: {len(nuovo_preset['esclusioni'])}")
        if input("Vuoi escludere una cartella specifica? (s/n): ").lower() != 's': 
            break
        
        # Usiamo il dialog per scegliere la cartella da NON copiare
        path_excl = get_folder_dialog("Seleziona cartella da ESCLUDERE")
        
        if path_excl:
            nuovo_preset["esclusioni"].append(path_excl)
            print(f"Esclusa: {os.path.basename(path_excl)}")
    # --------------------------------------

    settings = load_settings()
    if settings:
        settings["presets"].append(nuovo_preset)
        save_settings(settings)
        print("\nPreset salvato!")

def modifica_preset() -> None:
    settings = load_settings()
    if not settings or not settings["presets"]:
        print("Nessun preset.")
        return
    for i, p in enumerate(settings["presets"]):
        print(f"{i + 1}. {p['titolo']}")
    try:
        sel = int(input("Scelta (0 annulla): ")) - 1
        if sel == -1: return
        preset = settings["presets"][sel]
    except Exception: return

    # Assicuriamoci che la chiave esclusioni e storico_stats esistano anche nei vecchi preset
    if "esclusioni" not in preset:
        preset["esclusioni"] = []
    if "storico_stats" not in preset:
        preset["storico_stats"] = {}

    while True:
        print(f"\n--- Modifica: {preset['titolo']} ---")
        print("1. Modifica Generali (Titolo, Giorni, Destinazione)")
        print("2. Aggiungi Cartella ORIGINE")
        print("3. Rimuovi Cartella ORIGINE")
        print("4. Aggiungi ESCLUSIONE")
        print("5. Rimuovi ESCLUSIONE")
        print("6. Adotta su questa macchina")
        print("7. Indietro")
        s = input("Scelta: ")
        
        if s == '1':
            new_t = input(f"Titolo [{preset['titolo']}]: ")
            if new_t: preset["titolo"] = new_t
            new_g = input(f"Giorni [{preset['giorni_periodicita']}]: ")
            if new_g:
                try:
                    preset["giorni_periodicita"] = int(new_g)
                except ValueError:
                    print("Valore non valido. I giorni di periodicità non sono stati modificati.")
            if input("Cambiare destinazione? (s/n): ") == 's':
                nd = get_folder_dialog()
                if nd: preset["root_destinazione"] = nd
            save_settings(settings)
            
        elif s == '2':
            path = get_folder_dialog("Seleziona Nuova Origine")
            if path:
                nome = os.path.basename(path) or path.replace(":", "")
                preset["coppie_cartelle"].append({"origine": path, "nome_cartella": nome})
                save_settings(settings)
                
        elif s == '3':
            for ix, c in enumerate(preset["coppie_cartelle"]):
                print(f"{ix+1}. {c['origine']}")
            try:
                dx = int(input("Rimuovi num (0 annulla): ")) - 1
                if dx == -1: continue
                
                if 0 <= dx < len(preset["coppie_cartelle"]):
                    target = preset["coppie_cartelle"][dx]
                    nome_dir = target['nome_cartella']
                    root = preset['root_destinazione']
                    
                    print(f"\nStai rimuovendo: {target['origine']}")
                    print("ATTENZIONE: Vuoi ELIMINARE anche la cartella di backup fisica?")
                    print(f"Percorso: {os.path.join(root, nome_dir)}")
                    
                    conferma = input("Scrivi 'SI' per cancellare i dati, invio per tenerli: ").strip().upper()
                    
                    if conferma == 'SI':
                        path_del = os.path.join(root, nome_dir)
                        path_del = fix_long_path(path_del)
                        if os.path.exists(path_del):
                            try:
                                shutil.rmtree(path_del)
                                print("Cartella fisica eliminata.")
                            except Exception as e:
                                print(f"Errore eliminazione fisica: {e}")
                        else:
                            print("Cartella fisica non trovata su disco.")
                    else:
                        print("I dati fisici NON sono stati toccati.")
                    
                    preset["coppie_cartelle"].pop(dx)
                    save_settings(settings)
                    print("Voce rimossa dal preset.")
            except ValueError: pass

        elif s == '4':
            path_excl = get_folder_dialog("Seleziona cartella da ESCLUDERE")
            if path_excl:
                preset["esclusioni"].append(path_excl)
                save_settings(settings)
                print(f"Aggiunta esclusione: {os.path.basename(path_excl)}")

        elif s == '5':
            if not preset["esclusioni"]:
                print("Nessuna esclusione presente.")
                continue
            for ix, e in enumerate(preset["esclusioni"]):
                print(f"{ix+1}. {e}")
            try:
                dx = int(input("Rimuovi num (0 annulla): ")) - 1
                if dx == -1: continue
                if 0 <= dx < len(preset["esclusioni"]):
                    rm = preset["esclusioni"].pop(dx)
                    save_settings(settings)
                    print(f"Rimosso: {rm}")
            except ValueError: pass

        elif s == '6':
            preset["machine_id"] = get_machine_id()
            save_settings(settings)
            print("Adottato.")
            
        elif s == '7': break
def elimina_preset():
    settings = load_settings()
    if not settings or not settings["presets"]: return
    for i, p in enumerate(settings["presets"]):
        print(f"{i + 1}. {p['titolo']}")
    try:
        sel = int(input("Elimina num (0 annulla): ")) - 1
        if sel == -1: return
        settings["presets"].pop(sel)
        save_settings(settings)
        print("Eliminato.")
    except Exception: pass

def visualizza_presets():
    settings = load_settings()
    if not settings or not settings["presets"]:
        print("Nessun preset.")
        return
    print(f"\n{'ID':<4} {'TITOLO':<25} {'MACCHINA':<20} {'ULTIMO':<12} {'STATO'}")
    print("-" * 80)
    for idx, p in enumerate(settings["presets"]):
        tit = (p['titolo'][:22] + '..') if len(p['titolo']) > 22 else p['titolo']
        mac = (p.get('machine_id', 'N/A')[:18] + '..') if len(p.get('machine_id', 'N/A')) > 18 else p.get('machine_id', 'N/A')
        ult = p['ultimo_backup'] or "Mai"
        stato = "N/A"
        if not p['ultimo_backup']: stato = "Nuovo"
        else:
            try:
                d = datetime.datetime.strptime(ult, "%Y-%m-%d").date()
                delta = (datetime.date.today() - d).days
                rem = p['giorni_periodicita'] - delta
                stato = f"Scaduto ({abs(rem)}gg)" if rem < 0 else f"Tra {rem} gg"
            except Exception: pass
        print(f"{idx+1:<4} {tit:<25} {mac:<20} {ult:<12} {stato}")
    print("-" * 80)
    input("\nInvio...")

def check_scadenze_avvio():
    settings = load_settings()
    if not settings: return
    
    current_machine = get_machine_id()
    
    # Dizionario per accumulare i contatori: { "NomeMacchina": numero_scaduti }
    # Inizializziamo la macchina corrente a 0 per essere sicuri che appaia sempre
    report_macchine = {current_machine: 0}
    
    # Lista per memorizzare gli indici dei backup da lanciare (solo per questa macchina)
    indici_scaduti_locali = []
    
    presets = settings.get("presets", [])
    
    # --- FASE 1: Analisi ---
    for idx, p in enumerate(presets):
        m_id = p.get("machine_id", "Sconosciuto")
        
        # Se incontriamo una macchina nuova, la aggiungiamo al registro
        if m_id not in report_macchine:
            report_macchine[m_id] = 0
            
        # Logica di verifica scadenza
        scaduto = False
        ult = p.get("ultimo_backup")
        if not ult:
            scaduto = True # Mai fatto
        else:
            try:
                d = datetime.datetime.strptime(ult, "%Y-%m-%d").date()
                delta = (datetime.date.today() - d).days
                if delta >= p["giorni_periodicita"]:
                    scaduto = True
            except Exception: pass
            
        if scaduto:
            report_macchine[m_id] += 1
            # Se è scaduto ed è di QUESTA macchina, ci segniamo l'indice per dopo
            if m_id == current_machine:
                indici_scaduti_locali.append(idx)

    # --- FASE 2: Stampa Riepilogo ---
    # Se non c'è nulla di scaduto ovunque e abbiamo solo la macchina locale a 0, 
    # potremmo voler tacere, ma la tua richiesta implica di mostrare lo stato.
    # Se preferisci il silenzio assoluto quando è tutto ok, dimmelo.
    
    print("\n--- STATO SCADENZE ---")
    
    # Stampiamo PRIMA la macchina corrente
    locali = report_macchine[current_machine]
    print(f"{current_machine}: {locali} scaduti (Questa macchina)")
    
    # Stampiamo le ALTRE macchine
    for m_id, count in report_macchine.items():
        if m_id != current_machine:
            # Mostriamo le altre macchine solo se hanno preset registrati
            print(f"{m_id}: {count} scaduti")
            
    print("-" * 30)

    # --- FASE 3: Azione ---
    if indici_scaduti_locali:
        # Chiediamo di eseguire solo se ci sono scadenze LOCALI
        if input("Vuoi eseguire ora i backup scaduti per QUESTA macchina? (s/n): ").lower() == 's':
            for i in indici_scaduti_locali:
                esegui_backup(i)
    else:
        # Se tutto è a posto localmente, un breve delay per far leggere il report
        time.sleep(1)
def main():
    print(f"Benvenuto in {APP_NAME} v{APP_VERSION}\n\tby Gabriele Battaglia (IZ4APU)\n")
    print(f"ID: {get_machine_id()}")
    check_scadenze_avvio()
    while True:
        print(f"\n=== MENU {APP_NAME} ===")
        print("1. Esegui Backup")
        print("2. Esegui SIMULAZIONE")
        print("3. Visualizza Presets")
        print("4. Aggiungi Preset")
        print("5. Modifica Preset")
        print("6. Elimina Preset")
        print("7. Esci")
        s = input("\nScelta: ")
        if s == '1': esegui_backup(simulazione=False)
        elif s == '2': esegui_backup(simulazione=True)
        elif s == '3': visualizza_presets()
        elif s == '4': crea_nuovo_preset()
        elif s == '5': modifica_preset()
        elif s == '6': elimina_preset()
        elif s == '7': break

if __name__ == "__main__":
    main()