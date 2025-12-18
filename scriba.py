# Scriba by Gabriele Battaglia (IZ4APU)
# Data concepimento mercoledì 21 novembre 2025.
import os
import json
import subprocess
import datetime
import time
import sys
import platform
import shutil

# Tenta di importare wxPython
try:
    import wx
except ImportError:
    print("ERRORE CRITICO: La libreria wxPython non è installata.")
    sys.exit(1)

# --- CONFIGURAZIONE E COSTANTI ---
APP_NAME = "Scriba"
APP_VERSION = "2.1.0 di dicembre 2025"
SETTINGS_FILE = "scriba_settings.json"
REFRESH_RATE = 5.0
PRESET_TEMPLATE = {
    "titolo": "Casual",
    "machine_id": "God's Machine",
    "giorni_periodicita": 365,
    "ultimo_backup": None,
    "root_destinazione": "",
    "coppie_cartelle": [],
    "esclusioni": []
}

# --- GESTIONE DATI E SICUREZZA ---

def get_machine_id():
    hostname = platform.node()
    try:
        username = os.getlogin()
    except:
        username = os.environ.get('USERNAME', 'Unknown')
    return f"{hostname} | {username}"

def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        return {"presets": []}
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"ERRORE CRITICO caricamento settings: {e}")
        return None 

def save_settings(data):
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

def fix_long_path(path):
    if os.name == 'nt' and len(path) > 0 and not path.startswith('\\\\?\\'):
        path = os.path.abspath(path)
        if path.startswith('\\\\'):
            return '\\\\?\\UNC\\' + path[2:] 
        return '\\\\?\\' + path 
    return path

# --- INTERFACCIA UTENTE E UTILITIES ---

def get_folder_dialog(message="Seleziona una cartella"):
    app = wx.App(False)
    dlg = wx.DirDialog(None, message, "", wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST)
    selected_path = None
    if dlg.ShowModal() == wx.ID_OK:
        selected_path = dlg.GetPath()
    dlg.Destroy()
    return selected_path

def smart_truncate(text, max_len=45):
    if len(text) <= max_len:
        return text
    part_len = (max_len - 3) // 2
    head = text[:part_len]
    tail = text[-part_len:]
    return f"{head}...{tail}"

def format_size(bytes_val):
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

def stampa_dettaglio_esteso(preset):
    print("\n" + "="*60)
    print(f"RIEPILOGO PRESET: {preset['titolo']}")
    print("="*60)
    print(f"ID Macchina:       {preset.get('machine_id', 'N/A')}")
    print(f"Periodicità:       {preset['giorni_periodicita']} giorni")
    print(f"Ultima Esecuzione: {preset['ultimo_backup'] or 'Mai'}")
    print(f"Root Destinazione: {preset['root_destinazione']}")
    print("-" * 60)
    print(f"Cartelle da elaborare ({len(preset['coppie_cartelle'])}):")
    for c in preset['coppie_cartelle']:
        print(f"  [SRC] {c['origine']}")
        print(f"  [DST] ...\\{c['nome_cartella']}")
    print("="*60 + "\n")

# --- NUOVO BLOCCO LOGICA BACKUP ---
def get_dir_stats(path, user_exclusions=None):
    """
    Scansiona una cartella per contare file e dimensioni totali.
    Necessario per il confronto 'Prima vs Dopo'.
    """
    total_size = 0
    num_files = 0
    num_folders = 0
    safe_path = fix_long_path(path)
    
    if not os.path.exists(safe_path):
        return 0, 0, 0

    normalized_user_excl = []
    if user_exclusions:
        for p in user_exclusions:
            normalized_user_excl.append(os.path.abspath(p).lower())

    sys_excl_names = ["$RECYCLE.BIN", "System Volume Information", "Recovery"]

    try:
        for root, dirs, files in os.walk(safe_path, topdown=True):
            # Filtro cartelle (in-place)
            for i in range(len(dirs) - 1, -1, -1):
                d_name = dirs[i]
                d_full = os.path.join(root, d_name)
                if d_name in sys_excl_names:
                    del dirs[i]
                    continue
                if normalized_user_excl and os.path.abspath(d_full).lower() in normalized_user_excl:
                    del dirs[i]
                    continue

            num_folders += len(dirs)
            
            for f in files:
                if f.lower() in ["pagefile.sys", "hiberfil.sys", "swapfile.sys"]: continue
                num_files += 1
                try:
                    total_size += os.path.getsize(os.path.join(root, f))
                except: pass
    except: pass
    return num_files, num_folders, total_size

def get_robocopy_plan(src, dst, user_exclusions=None):
    """
    Esegue una simulazione rapida (/L) per contare quante operazioni 
    (copie file + eliminazioni extra) verranno eseguite.
    """
    # --- FIX CRITICO PER ROBOCOPY ---
    # Robocopy non digerisce i prefissi \\?\ da riga di comando, li rimuoviamo solo per il comando CMD.
    # Rimuoviamo anche lo slash finale se non è una root (es. C:\) per evitare problemi di escape delle virgolette.
    
    cmd_src = src.replace("\\\\?\\UNC\\", "\\\\").replace("\\\\?\\", "")
    if cmd_src.endswith("\\") and not cmd_src.endswith(":\\"): cmd_src = cmd_src.rstrip("\\")
    
    cmd_dst = dst.replace("\\\\?\\UNC\\", "\\\\").replace("\\\\?\\", "")
    if cmd_dst.endswith("\\") and not cmd_dst.endswith(":\\"): cmd_dst = cmd_dst.rstrip("\\")

    # /L = List Only, /NDL = No Dir List (conta solo i file), /NJH/NJS = No Header/Summary
    cmd = ["robocopy", cmd_src, cmd_dst, "/MIR", "/XJ", "/R:1", "/W:1", "/L", "/NJH", "/NJS", "/NDL", "/NC", "/NS"]
    
    # Esclusioni di sistema
    drive, tail = os.path.splitdrive(src)
    if tail in ['\\', '/', ''] or src.endswith(':\\'):
        cmd.extend(["/XD", "$RECYCLE.BIN", "System Volume Information", "Recovery"])
        cmd.extend(["/XF", "pagefile.sys", "hiberfil.sys", "swapfile.sys"])
    
    if user_exclusions:
        cmd.append("/XD")
        # Anche le esclusioni devono essere pulite dal prefisso \\?\ per Robocopy
        clean_excl = [e.replace("\\\\?\\UNC\\", "\\\\").replace("\\\\?\\", "") for e in user_exclusions]
        cmd.extend(clean_excl)

    ops_count = 0
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, encoding='cp850', errors='replace',
            startupinfo=startupinfo
        )
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None: break
            if line and line.strip():
                ops_count += 1
    except Exception: return 0
    return ops_count
def parse_robocopy_stat_line(line):
    """
    Estrae TUTTI i numeri interi da una riga di testo, ignorando parole e simboli.
    Funziona con qualsiasi formattazione (es. "Files : 10", "Files:10", "File: 10").
    """
    try:
        # Sostituiamo i due punti e i tab con spazi per sicurezza
        clean_line = line.replace(":", " ").replace("\t", " ")
        parts = clean_line.split()
        
        # Estraiamo solo ciò che è numerico
        nums = [int(x) for x in parts if x.isdigit()]
        
        # Una riga di statistiche valida di Robocopy ha almeno 2 o 3 numeri
        if len(nums) >= 2:
            return nums
    except: pass
    return None
def run_robocopy_engine(src, dst, log_file, user_exclusions=None, is_simulation=False, 
                        global_total=0, global_offset=0, global_start_time=0, current_task_name=""):
    """
    Esegue Robocopy con fix percorsi, ETA a finestra mobile e visualizzazione contestuale.
    """
    try: rate = REFRESH_RATE
    except NameError: rate = 5.0

    # --- FIX PERCORSI ---
    cmd_src = src.replace("\\\\?\\UNC\\", "\\\\").replace("\\\\?\\", "")
    if cmd_src.endswith("\\") and not cmd_src.endswith(":\\"): cmd_src = cmd_src.rstrip("\\")
    
    cmd_dst = dst.replace("\\\\?\\UNC\\", "\\\\").replace("\\\\?\\", "")
    if cmd_dst.endswith("\\") and not cmd_dst.endswith(":\\"): cmd_dst = cmd_dst.rstrip("\\")

    cmd = ["robocopy", cmd_src, cmd_dst, "/MIR", "/XJ", "/R:1", "/W:1", "/FFT", "/NDL", "/NJH", "/NP", "/BYTES"]
    
    if user_exclusions:
        cmd.append("/XD")
        clean_excl = [e.replace("\\\\?\\UNC\\", "\\\\").replace("\\\\?\\", "") for e in user_exclusions]
        cmd.extend(clean_excl)
    
    if is_simulation:
        cmd.append("/L") 
    
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW 
    
    stats = {
        "files_total": 0, "files_copied": 0, "files_extras": 0, "files_failed": 0,
        "dirs_total": 0,  "dirs_created": 0, 
        "bytes_copied": 0, "bytes_total": 0
    }
    
    processed_local = 0
    last_update_time = time.time()
    
    # Variabili per ETA a finestra mobile
    eta_history = [] # Lista di tuple (tempo, ops_totali_fino_a_quel_momento)
    WINDOW_SECONDS = 20.0
    
    try:
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, encoding='cp850', errors='replace',
            startupinfo=startupinfo
        )
        
        with open(log_file, 'w', encoding='utf-8') as f_log:
            f_log.write(f"--- AVVIO: {datetime.datetime.now()} ---\nSRC: {src}\nDST: {dst}\n\n")

            while True:
                line = process.stdout.readline()
                if not line and process.poll() is not None: break
                
                if line:
                    stripped = line.strip()
                    lower_line = stripped.lower()
                    f_log.write(line)

                    # --- RILEVAMENTO RIGHE DI RIEPILOGO ---
                    is_header = "total" in lower_line and "copied" in lower_line
                    is_separator = "---" in stripped
                    is_stat_line = (":" in line) and any(k in lower_line for k in ["file", "dir", "cartell", "byte", "total", "durata", "velocit", "finito", "speed", "ended"])
                    is_summary = is_header or is_separator or is_stat_line

                    # --- PARSING ROBUSTO ---
                    if is_stat_line:
                        nums = parse_robocopy_stat_line(line)
                        if nums:
                            if "dir" in lower_line or "cartell" in lower_line: 
                                stats["dirs_total"] = nums[0]
                                if len(nums) > 1: stats["dirs_created"] = nums[1]
                            elif "file" in lower_line:
                                stats["files_total"] = nums[0]
                                if len(nums) > 1: stats["files_copied"] = nums[1]
                                if len(nums) >= 5: stats["files_failed"] = nums[4]
                                if len(nums) >= 6: stats["files_extras"] = nums[5]
                            elif "byte" in lower_line:
                                stats["bytes_total"] = nums[0]
                                if len(nums) > 1: stats["bytes_copied"] = nums[1]

                    # --- AGGIORNAMENTO BARRA ---
                    if stripped and not is_summary:
                        processed_local += 1
                        current_time = time.time()
                        
                        if current_time - last_update_time > 0.5: # Aggiorna ogni 0.5s per fluidità
                            total_done = global_offset + processed_local
                            
                            # Percentuale
                            perc = 0.0
                            if global_total > 0:
                                perc = (total_done / global_total) * 100
                            if perc > 100: perc = 99.9

                            # ETA (Finestra Mobile)
                            eta_history.append((current_time, total_done))
                            # Rimuovi dati vecchi fuori dalla finestra
                            while eta_history and (current_time - eta_history[0][0] > WINDOW_SECONDS):
                                eta_history.pop(0)
                            
                            eta_str = "--:--"
                            if len(eta_history) > 1:
                                delta_t = eta_history[-1][0] - eta_history[0][0]
                                delta_ops = eta_history[-1][1] - eta_history[0][1]
                                
                                if delta_t > 0 and delta_ops > 0:
                                    speed = delta_ops / delta_t
                                    remaining = global_total - total_done
                                    if speed > 0:
                                        sec_left = remaining / speed
                                        if sec_left < 60:
                                            eta_str = f"{int(sec_left)}s"
                                        else:
                                            m, s = divmod(int(sec_left), 60)
                                            h, m = divmod(m, 60)
                                            if h > 0: eta_str = f"{h}h{m}m"
                                            else: eta_str = f"{m}m{s}s"
                            
                            # Formattazione Visuale
                            task_lbl = f"[{current_task_name[:10]}]" if current_task_name else ""
                            
                            parts = stripped.split("\t")
                            raw_file = parts[-1].strip() if parts else stripped
                            file_name = os.path.basename(raw_file)
                            # Troncatura intelligente
                            avail_space = 79 - 25 - len(task_lbl) # Spazio rimanente approx
                            if avail_space < 10: avail_space = 10
                            short_name = smart_truncate(file_name, avail_space)
                            
                            # Output con padding finale per pulizia
                            out_str = f"\r{perc:5.1f}% [{eta_str:>5}] {task_lbl} {short_name}"
                            sys.stdout.write(f"{out_str:<85}") # Padding fisso a 85 char
                            sys.stdout.flush()
                            last_update_time = current_time

        return stats, processed_local

    except Exception as e:
        print(f"\nErrore Robocopy: {e}")
        return stats, 0
def esegui_backup(preset_index=None, simulazione=False):
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

    # --- CONTROLLO ESISTENZA ORIGINI ---
    root_dest = preset["root_destinazione"]
    cartelle_valide = []
    for c in preset["coppie_cartelle"]:
        src = fix_long_path(c["origine"])
        if os.path.exists(src):
            cartelle_valide.append(c)
        else:
            print(f"AVVISO: Origine non trovata, verrà saltata: {c['origine']}")
    
    if not cartelle_valide:
        print("Nessuna cartella valida da copiare.")
        return

    if not simulazione and preset["ultimo_backup"]:
        try:
            d = datetime.datetime.strptime(preset["ultimo_backup"], "%Y-%m-%d").date()
            if (datetime.date.today() - d).days < preset["giorni_periodicita"]:
                print("AVVISO: Periodicità non ancora scaduta.")
                if input("Procedere comunque? (s/n): ").lower() != 's': return
        except: pass

    spegni_pc = False
    if not simulazione:
        spegni_pc = (input("\nVuoi spegnere il PC al termine? (s/n): ").lower() == 's')

    start_total = time.time()
    log_dir = os.path.join(root_dest, "Logs")
    if not os.path.exists(log_dir):
        try: os.makedirs(log_dir)
        except: pass 

    # ============================================================
    # FASE 1: INVENTARIO (DRY-RUN)
    # ============================================================
    print(f"\n--- FASE 1/2: Inventario e Analisi ({len(cartelle_valide)} cartelle) ---")
    grand_total_ops = 0
    tasks_plan = [] 

    for i, coppia in enumerate(cartelle_valide):
        src = fix_long_path(coppia["origine"])
        dst = fix_long_path(os.path.join(root_dest, coppia["nome_cartella"]))
        nome_breve = smart_truncate(coppia["nome_cartella"], 30)
        
        sys.stdout.write(f"\r Analisi {i+1}/{len(cartelle_valide)}: {nome_breve:<35} ")
        sys.stdout.flush()
        
        ops = get_robocopy_plan(src, dst, user_exclusions=preset.get("esclusioni", []))
        grand_total_ops += ops
        tasks_plan.append({
            "coppia": coppia,
            "src": src, "dst": dst,
            "ops_stimate": ops
        })
    
    print(f"\n Completato. Operazioni totali previste: {grand_total_ops}")
    time.sleep(1)

    # ============================================================
    # FASE 2: ESECUZIONE (REAL RUN)
    # ============================================================
    print(f"\n--- FASE 2/2: Esecuzione {tipo_run} ---")
    
    global_processed_counter = 0
    phase2_start_time = time.time()
    
    # Variabili per statistiche "Prima vs Dopo"
    total_pre_files = 0
    total_post_files = 0
    total_pre_size = 0
    total_post_size = 0
    
    # Contatori Robocopy
    robocopy_files_ok = 0
    robocopy_files_fail = 0
    robocopy_files_deleted = 0
    robocopy_bytes_transferred = 0

    for i, task in enumerate(tasks_plan):
        coppia = task["coppia"]
        nome_dir = coppia["nome_cartella"]
        log_file = os.path.join(log_dir, f"{nome_dir}-log.txt")
        dst_path = task["dst"]
        
        task_start_time = time.time() # Tempo inizio task

        # 1. MISURAZIONE PRE
        if not simulazione:
            f_pre, d_pre, s_pre = get_dir_stats(dst_path, preset.get("esclusioni", []))
            total_pre_files += f_pre
            total_pre_size += s_pre
        
        # 2. ESECUZIONE
        stats, ops_fatte = run_robocopy_engine(
            task["src"],
            task["dst"],
            log_file,
            user_exclusions=preset.get("esclusioni", []),
            is_simulation=simulazione,
            global_total=grand_total_ops,
            global_offset=global_processed_counter,
            global_start_time=phase2_start_time,
            current_task_name=nome_dir
        )
        
        global_processed_counter += ops_fatte
        robocopy_files_ok += stats["files_copied"]
        robocopy_files_fail += stats["files_failed"]
        robocopy_files_deleted += stats["files_extras"]
        robocopy_bytes_transferred += stats["bytes_copied"]

        # 3. MISURAZIONE POST
        if not simulazione:
            f_post, d_post, s_post = get_dir_stats(dst_path, preset.get("esclusioni", []))
            total_post_files += f_post
            total_post_size += s_post
            
        # 4. REPORT TASK COMPLETATO
        task_duration = time.time() - task_start_time
        m_task, s_task = divmod(int(task_duration), 60)
        # \r sovrascrive la barra di progresso, padding massiccio per pulizia residui
        msg_ok = f"\r   [OK] ({i+1}/{len(tasks_plan)}) {nome_dir:<20} - Tempo: {m_task:02d}:{s_task:02d}"
        print(f"{msg_ok:<100}")

    print("\n" + "="*60) 

    # ============================================================
    # REPORT FINALE (COMPARATIVO)
    # ============================================================
    if not simulazione and settings:
        preset["ultimo_backup"] = datetime.date.today().strftime("%Y-%m-%d")
        save_settings(settings)
    
    total_time = time.time() - start_total
    m_tot, s_tot = divmod(total_time, 60)
    h_tot, m_tot = divmod(m_tot, 60)
    
    print(f"\nRIEPILOGO SESSIONE - {tipo_run}")
    print("="*60)
    print(f"Tempo Totale:     {int(h_tot):02d}:{int(m_tot):02d}:{s_tot:06.3f}")
    
    if simulazione:
        print("NOTA: In simulazione le statistiche 'Prima vs Dopo' non vengono calcolate.")
        print(f"Operazioni simulate: {robocopy_files_ok}")
    else:
        # Calcolo Differenze
        diff_size = total_post_size - total_pre_size
        diff_files = total_post_files - total_pre_files
        
        perc_size = 0.0
        if total_pre_size > 0:
            perc_size = (diff_size / total_pre_size) * 100
        elif total_post_size > 0: # Se prima era vuoto ed è cresciuto
            perc_size = 100.0

        sign_s = "+" if diff_size >= 0 else ""
        sign_f = "+" if diff_files >= 0 else ""

        print("-" * 60)
        print(f"{'METRICA':<15} {'PRIMA':<15} {'DOPO':<15} {'DIFFERENZA'}")
        print("-" * 60)
        
        # Riga Dimensioni
        s_pre_str = format_size(total_pre_files).replace(" B", "").replace(" KB", "") # Hack estetico veloce o usa format_size normale
        # Usiamo format_size normale che è più sicuro
        print(f"{'Dimensioni':<15} {format_size(total_pre_size):<15} {format_size(total_post_size):<15} {sign_s}{format_size(diff_size)} ({sign_s}{perc_size:.2f}%)")
        
        # Riga File
        print(f"{'File Totali':<15} {str(total_pre_files):<15} {str(total_post_files):<15} {sign_f}{diff_files}")
        
        print("-" * 60)
        
        # --- SEZIONE PERFORMANCE & IMPATTO ---
        print("PERFORMANCE & IMPATTO:")
        
        # 1. Velocità Media
        speed_str = "0.00 B/s"
        if total_time > 0 and robocopy_bytes_transferred > 0:
            speed_val = robocopy_bytes_transferred / total_time
            speed_str = f"{format_size(speed_val)}/s"
        print(f" - Velocità Media:       {speed_str}")

        # 2. Impatto Modifiche
        impact_perc = 0.0
        if total_post_files > 0:
            impact_perc = (robocopy_files_ok / total_post_files) * 100
        print(f" - File Elaborati:       {robocopy_files_ok} ({impact_perc:.1f}% del totale attuale)")
        
        # 3. Altri Dettagli
        print(f" - File Eliminati (Mir): {robocopy_files_deleted}")
        print(f" - Errori Critici:       {robocopy_files_fail}")

        if robocopy_files_fail > 0:
            print("\n" + "!"*60)
            print(f" ATTENZIONE: {robocopy_files_fail} file NON sono stati copiati per errore.")
            print(" CONTROLLARE I LOG NELLA CARTELLA DI DESTINAZIONE!")
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
            if input("Aprire cartella log? (s/n): ").lower() == 's':
                try: os.startfile(log_dir)
                except: subprocess.call(['explorer', log_dir])
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

    nuovo_preset = PRESET_TEMPLATE.copy()
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

def modifica_preset():
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
    except: return

    # Assicuriamoci che la chiave esclusioni esista anche nei vecchi preset
    if "esclusioni" not in preset:
        preset["esclusioni"] = []

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
            if new_g: preset["giorni_periodicita"] = int(new_g)
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
                    print(f"ATTENZIONE: Vuoi ELIMINARE anche la cartella di backup fisica?")
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
    except: pass

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
            except: pass
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
            except: pass
            
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