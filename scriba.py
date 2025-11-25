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
APP_VERSION = "1.3.4 di novembre 2025"
SETTINGS_FILE = "scriba_settings.json"

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

def get_dir_stats(path):
    total_size = 0
    num_files = 0
    num_folders = 0
    safe_path = fix_long_path(path)
    
    if not os.path.exists(safe_path):
        return 0, 0, 0

    try:
        for root, dirs, files in os.walk(safe_path):
            num_folders += len(dirs)
            num_files += len(files)
            for f in files:
                try:
                    fp = os.path.join(root, f)
                    total_size += os.path.getsize(fp)
                except OSError: pass
    except Exception: pass 
    return num_files, num_folders, total_size

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

# --- CORE: MOTORE ROBOCOPY CON ETA ---

def run_robocopy_engine(src, dst, log_file, total_files_source, user_exclusions=None, is_simulation=False):
    # --- COSTRUZIONE COMANDO ---
    # Nota: Non usiamo /NJS (No Job Summary) perché ci serve leggere il riepilogo alla fine!
    cmd = ["robocopy", src, dst, "/MIR", "/XJ", "/R:1", "/W:1", "/FFT", "/NDL", "/NJH", "/E", "/NP"]
    
    # --- SMART EXCLUDE (Sistema) ---
    drive, tail = os.path.splitdrive(src)
    # Se siamo nella root (es. D:\ o C:\)
    if tail in ['\\', '/', ''] or src.endswith(':\\'):
        cmd.extend(["/XD", "$RECYCLE.BIN", "System Volume Information", "Recovery"])
        cmd.extend(["/XF", "pagefile.sys", "hiberfil.sys", "swapfile.sys"])
    
    # --- ESCLUSIONI UTENTE (Manuali) ---
    if user_exclusions:
        cmd.append("/XD")
        cmd.extend(user_exclusions)
    
    if is_simulation:
        cmd.append("/L") 
    
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW 
    
    processed_count = 0  # Questo serve solo per far muovere la barra di progresso
    real_copied_count = 0 # Questo sarà il numero VERO preso dal riepilogo finale
    
    last_update_time = 0
    start_run_time = time.time()
    refresh_rate = 2.0
    avg_speed = 0.0
    smoothing_factor = 0.1

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            encoding='cp850', 
            errors='replace',
            startupinfo=startupinfo
        )
        
        with open(log_file, 'w', encoding='utf-8') as f_log:
            f_log.write(f"--- LOG AVVIATO: {datetime.datetime.now()} ---\n")
            if is_simulation: f_log.write("--- SIMULAZIONE ---\n")
            f_log.write(f"ORIGINE: {src}\nDESTINAZIONE: {dst}\n\n")

            while True:
                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break
                
                if line:
                    stripped = line.strip()
                    # Scriviamo tutto nel log
                    f_log.write(line)

                    # --- ANALISI RIEPILOGO FINALE ---
                    # Cerchiamo la riga che inizia per "Files :" (Inglese) o "File :" (Italiano)
                    # Esempio: "   Files :      167         0       167         0         0         0"
                    # Colonne: [Label] [Tot] [Copiati] [Saltati] ...
                    if "File" in line and ":" in line:
                        parts = line.split()
                        # Verifichiamo che sia la riga di riepilogo (File + :)
                        if len(parts) >= 4 and "File" in parts[0] and parts[1] == ":":
                            try:
                                # La colonna 3 (indice 3) è "Copied"
                                real_copied_count = int(parts[3])
                            except ValueError: pass

                    # --- GESTIONE BARRA DI PROGRESSO ---
                    # Filtriamo header, footer e riepiloghi per contare l'avanzamento
                    if stripped and not stripped.startswith("---") and not "File(s)" in stripped and not "File :" in stripped and not "*EXTRA" in stripped and not "Dir(s)" in stripped:
                        processed_count += 1
                        current_time = time.time()
                        
                        if current_time - last_update_time > refresh_rate:
                            # 1. Percentuale (basata sui file scansionati/toccati)
                            perc = 0.0
                            if total_files_source > 0:
                                perc = (processed_count / total_files_source) * 100
                                if perc > 100: perc = 99.9 
                            
                            # 2. ETA Intelligente
                            elapsed_total = current_time - start_run_time
                            eta_str = "--:--"
                            
                            if elapsed_total > 5 and processed_count > 5 and total_files_source > 0:
                                files_remaining = total_files_source - processed_count
                                if processed_count > 0:
                                    current_speed = processed_count / elapsed_total
                                    if avg_speed == 0: avg_speed = current_speed
                                    else: avg_speed = (smoothing_factor * current_speed) + ((1 - smoothing_factor) * avg_speed)
                                    
                                    if avg_speed > 0:
                                        time_remaining_sec = files_remaining / avg_speed
                                        now = datetime.datetime.now()
                                        end_time = now + datetime.timedelta(seconds=time_remaining_sec)
                                        
                                        delta_days = (end_time.date() - now.date()).days
                                        if delta_days > 0:
                                            eta_str = f"+{delta_days}g {end_time.strftime('%H:%M')}"
                                        else:
                                            eta_str = end_time.strftime("%H:%M")

                            parts = stripped.split("\t")
                            file_info = parts[-1].strip() if parts else stripped
                            short_path = smart_truncate(file_info, 40)
                            
                            sys.stdout.write(f"\r[{perc:5.1f}%] [{eta_str:>9}] {short_path: <45}")
                            sys.stdout.flush()
                            last_update_time = current_time

        print("\r" + " " * 110 + "\r", end="")
        
        # RESTITUIAMO IL CONTEGGIO REALE PRESO DAL RIEPILOGO, NON QUELLO STIMATO
        return real_copied_count

    except Exception as e:
        print(f"\nErrore esecuzione Robocopy: {e}")
        return 0
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
    if input("Vuoi procedere? (s/n): ").lower() != 's':
        return

    preset_machine = preset.get("machine_id", "Sconosciuto")
    if preset_machine != current_machine and not simulazione:
        print("\n" + "!"*60)
        print(f"ATTENZIONE: ID Macchina non corrispondente!")
        print(f"Preset: {preset_machine} | Tu: {current_machine}")
        print("!"*60)
        if input("Scrivi 'SI' per forzare: ") != "SI": return

    # --- VALIDAZIONE ORIGINI ---
    cartelle_mancanti = []
    root_dest = preset["root_destinazione"]
    
    for c in preset["coppie_cartelle"]:
        path_check = fix_long_path(c["origine"])
        if not os.path.exists(path_check):
            cartelle_mancanti.append(c)

    if cartelle_mancanti:
        print("\n" + "!"*60)
        print(f"ATTENZIONE: {len(cartelle_mancanti)} cartelle di origine NON ESISTONO più.")
        print("!"*60)
        if input("Vuoi vedere la lista? (s/n): ").lower() == 's':
            print("\nCartelle mancanti:")
            for cm in cartelle_mancanti:
                print(f" - {cm['origine']}")

        print("\nVuoi RIMUOVERLE dal preset e CANCELLARE i dati nella destinazione?")
        if input("Scrivi 'SI' per procedere: ") == 'SI':
            for cm in cartelle_mancanti:
                path_dst = os.path.join(root_dest, cm["nome_cartella"])
                path_dst = fix_long_path(path_dst)
                print(f"Eliminazione: {cm['nome_cartella']} ... ", end="")
                if simulazione:
                    print("[SIMULATO]")
                else:
                    if os.path.exists(path_dst):
                        try:
                            shutil.rmtree(path_dst)
                            print("CANCELLATO.")
                        except Exception as e:
                            print(f"ERRORE: {e}")
                    else:
                        print("Già assente.")

            preset["coppie_cartelle"] = [x for x in preset["coppie_cartelle"] if x not in cartelle_mancanti]
            if not simulazione:
                save_settings(settings)
            print("\nDatabase aggiornato. Procedo...")
            time.sleep(2)
        else:
            print("Nessuna azione. Le cartelle verranno saltate.")

    if not preset["coppie_cartelle"]:
        print("\nNessuna cartella valida. Annullato.")
        return

    if not simulazione and preset["ultimo_backup"]:
        try:
            d = datetime.datetime.strptime(preset["ultimo_backup"], "%Y-%m-%d").date()
            mancanti = preset["giorni_periodicita"] - (datetime.date.today() - d).days
            if mancanti > 0:
                print("\n" + "-"*50)
                print(f"AVVISO: Periodicità non scaduta. Mancano {mancanti} giorni.")
                print("-"*50)
                if input("Procedere comunque? (s/n): ").lower() != 's': return
        except ValueError: pass 

    spegni_pc = False
    if not simulazione:
        spegni_pc = (input("\nVuoi spegnere il PC al termine? (s/n): ").lower() == 's')

    start_total = time.time()
    
    grand_pre_files = 0
    grand_post_files = 0
    grand_pre_folders = 0
    grand_post_folders = 0
    grand_pre_size = 0
    grand_post_size = 0
    grand_files_copied = 0 
    grand_files_new = 0    
    grand_files_mod = 0    
    processed_pairs_count = 0

    print(f"\n--- Inizio {tipo_run}: {preset['titolo']} ---")
    
    log_dir = os.path.join(root_dest, "Logs")
    if not os.path.exists(log_dir):
        try: os.makedirs(log_dir)
        except: pass 

    try: 
        for coppia in preset["coppie_cartelle"]:
            raw_src = coppia["origine"]
            nome_dir = coppia["nome_cartella"]
            raw_dst = os.path.join(root_dest, nome_dir)
            stat_src = fix_long_path(raw_src)
            stat_dst = fix_long_path(raw_dst)
            
            print(f"\n> Elaborazione: {nome_dir}")
            
            if not os.path.exists(stat_src):
                print(f"  ERRORE: Origine non trovata: {raw_src}")
                continue

            print("  Analisi sorgente (per barra di progresso)...", end="", flush=True)
            files_src_count, _, _ = get_dir_stats(stat_src)
            print(f" Fatto ({files_src_count} file stimati).")

            print("  Analisi destinazione (per statistiche diff)...", end="", flush=True)
            files_pre, folders_pre, size_pre = get_dir_stats(stat_dst)
            print(" Fatto.")

            log_file = os.path.join(log_dir, f"{nome_dir}-log.txt")
            
            t_start_copy = time.time()
            lista_esclusioni = preset.get("esclusioni", [])
            files_copied_real = run_robocopy_engine(
                raw_src, 
                raw_dst, 
                log_file, 
                files_src_count, 
                user_exclusions=lista_esclusioni,  # <--- NUOVO PARAMETRO
                is_simulation=simulazione
            )
            t_end_copy = time.time()
            
            print("  Analisi statistiche finali...", end="", flush=True)
            files_post, folders_post, size_post = get_dir_stats(stat_dst)
            print(" Fatto.")
            
            grand_pre_files += files_pre
            grand_post_files += files_post
            grand_pre_folders += folders_pre
            grand_post_folders += folders_post
            grand_pre_size += size_pre
            grand_post_size += size_post
            processed_pairs_count += 1

            diff_files = files_post - files_pre
            diff_size_val = size_post - size_pre
            diff_folders = folders_post - folders_pre

            estimated_new = max(0, diff_files) 
            estimated_mod = max(0, files_copied_real - estimated_new)
            
            grand_files_copied += files_copied_real
            grand_files_new += estimated_new
            grand_files_mod += estimated_mod
            
            tempo_impiegato = t_end_copy - t_start_copy
            
            if size_pre == 0:
                diff_perc = 100.0 if size_post > 0 else 0.0
            else:
                diff_perc = ((size_post - size_pre) / size_pre) * 100
            
            m, s = divmod(tempo_impiegato, 60)
            h, m = divmod(m, 60)
            str_tempo = f"{int(h):02d}:{int(m):02d}:{s:06.3f}"
            str_diff_size = format_size(diff_size_val)
            if diff_size_val == 0: str_diff_size = "+0.00 B"

            print("-" * 60)
            print(f"  RAPPORTO {tipo_run} per: {nome_dir}")
            print(f"  Tempo:         {str_tempo}")
            print(f"  Dimensioni:    {format_size(size_pre).replace('+','')} -> {format_size(size_post).replace('+','')} (Diff: {str_diff_size}, {diff_perc:+.2f}%)")
            print(f"  File (Saldo):  {files_pre} -> {files_post} ({diff_files:+d})")
            print(f"  Dettaglio Ops: {files_copied_real} Copiati (Nuovi: ~{estimated_new}, Modificati: ~{estimated_mod})")
            print("-" * 60)

        if not simulazione and settings:
            preset["ultimo_backup"] = datetime.date.today().strftime("%Y-%m-%d")
            save_settings(settings)
        
        total_time = time.time() - start_total
        m_tot, s_tot = divmod(total_time, 60)
        h_tot, m_tot = divmod(m_tot, 60)
        
        diff_total_size = grand_post_size - grand_pre_size
        diff_total_files = grand_post_files - grand_pre_files
        diff_total_folders = grand_post_folders - grand_pre_folders
        
        if grand_pre_size == 0:
            diff_total_perc = 100.0 if grand_post_size > 0 else 0.0
        else:
            diff_total_perc = ((grand_post_size - grand_pre_size) / grand_pre_size) * 100
        
        str_diff_total_size = format_size(diff_total_size)
        if diff_total_size == 0: str_diff_total_size = "+0.00 B"

        print("\n========================================")
        print(f"BILANCIO TOTALE SESSIONE - {tipo_run}")
        print("========================================")
        print(f"Coppie Elaborate: {processed_pairs_count}")
        print(f"Tempo Totale:     {int(h_tot):02d}:{int(m_tot):02d}:{s_tot:06.3f}")
        print("-" * 40)
        print(f"Dimensioni:       {format_size(grand_pre_size).replace('+','')} -> {format_size(grand_post_size).replace('+','')}")
        print(f"                  (Diff: {str_diff_total_size}, {diff_total_perc:+.2f}%)")
        print(f"File (Saldo):     {grand_pre_files} -> {grand_post_files} ({diff_total_files:+d})")
        print(f"Cartelle Totali:  {grand_pre_folders} -> {grand_post_folders} ({diff_total_folders:+d})")
        print("-" * 40)
        print(f"DETTAGLIO OPERAZIONI SUI FILE:")
        print(f"Copiati/Aggiornati: {grand_files_copied}")
        print(f" -> Nuovi (Stimati):      {grand_files_new}")
        print(f" -> Modificati (Stimati): {grand_files_mod}")
        print("========================================")

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
                print(f"\nLogs in: {log_dir}")
                if input("Aprire cartella log? (s/n): ").lower() == 's':
                    try: os.startfile(log_dir)
                    except AttributeError: subprocess.call(['explorer', log_dir])

    except KeyboardInterrupt:
        print("\n\n!!! INTERRUZIONE (CTRL+C) !!!")
        input("Premi INVIO per tornare al menu...")

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