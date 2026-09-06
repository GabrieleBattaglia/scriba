# Scriba, orchestratore di backup basato su Robocopy.
# Autori: Gabriele Battaglia (IZ4APU) & ClaudIA (Claude Opus 5, modalità auto).
# Data concepimento mercoledì 21 novembre 2025.

import contextlib
import copy
import datetime
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog

from GBUtils import Acusticator, Donazione, dgt, enter_escape, gestisci_aggiornamento, manuale, menu

try:
    import msvcrt
except ImportError:  # fuori da Windows non c'è, e lo stato a richiesta si spegne
    msvcrt = None

# --- CONFIGURAZIONE E COSTANTI ---
APP_NAME = "Scriba"
APP_VERSION = "3.0.0"
RELEASE_DATE = "2026-09-06"
VERSIONE_SCHEMA = 1
NOME_MANUALE = "Manuale_Scriba.txt"
API_RELEASE = "https://api.github.com/repos/GabrieleBattaglia/scriba/releases/latest"
LARGHEZZA_BLOCCO = 40
# Robocopy scrive il log nella codepage OEM della macchina. Su Windows
# italiano è la 850, che prima stava scritta a mano in tre punti, ma altrove
# è un'altra: il codec oem chiede al sistema qual è la sua.
CODIFICA_LOG = "oem" if os.name == "nt" else "utf-8"
# Segnali acustici dell'avanzamento: sette ottave da c2 a b8, onda
# triangolare perché è la più morbida da tenere in sottofondo per ore.
SCALA = ["c", "c#", "d", "d#", "e", "f", "f#", "g", "g#", "a", "a#", "b"]
OTTAVA_MINIMA = 2
OTTAVE = 7
ONDA = 3
# Attacco corto e decadimento lungo, senza sostegno: la nota nasce e si
# spegne da sola, come una corda pizzicata o una campanella. E' quello che
# rende il segnale discreto anche quando se ne sentono a decine.
ADSR_SEGNALE = [3, 72, 0, 0]
DURATA_SEGNALE = 0.075
VOLUME_SEGNALE = 0.45
# Cadenza dei segnali: uno ogni due punti percentuali di avanzamento
# complessivo, e mai due a meno di cinque secondi l'uno dall'altro.
PASSO_SEGNALE = 0.02
SECONDI_FRA_SEGNALI = 5.0
# La barra di avanzamento si riscrive sul posto, fra due ritorni a capo e
# larga esattamente quanto un display braille. Il ritorno a capo finale
# riporta il cursore a colonna zero: e' quello che tiene il focus fermo sul
# principio della riga, cosi' le dita restano dove sono e leggono un dato che
# si aggiorna sotto di loro. E' il motivo per cui la barra esiste.
# Due secondi, non uno: NVDA ha bisogno di tempo per leggere quel che
# cambia, e una riga che si riscrive troppo spesso non si riesce a seguire.
# Questa cadenza riguarda soltanto la barra, non i segnali acustici, che
# hanno la loro, ne' il resto di quel che il programma scrive.
SECONDI_FRA_AGGIORNAMENTI = 2.0
# Ogni quanto una riga di stato viene scritta nel diario. A schermo non
# compare: la barra basta, e il diario non deve riempirsi di sue copie.
SECONDI_FRA_RIGHE = 120.0
# Ogni quanto il lettore del log si sveglia anche se robocopy tace, cosi' la
# barra si aggiorna anche mentre si sta soltanto scandendo.
SECONDI_FRA_BATTITI = 0.5


def cartella_programma() -> str:
    """Restituisce la cartella in cui vive Scriba.
    Da sorgente è quella di questo file, da eseguibile PyInstaller è quella
    dell'exe. Non è mai la directory di lavoro corrente, che dipende da dove
    l'utente ha lanciato il programma e che quindi farebbe cercare le
    impostazioni in un posto diverso a ogni avvio.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def file_di_supporto(nome: str) -> str:
    """Percorso di un file che viaggia insieme al programma.
    Da eseguibile PyInstaller i file dichiarati in datas non stanno accanto
    all'exe ma nella cartella temporanea di estrazione, che sta in
    sys._MEIPASS: chi cerca il manuale deve guardare lì.
    """
    base = getattr(sys, "_MEIPASS", None) or cartella_programma()
    return os.path.join(base, nome)


FILE_IMPOSTAZIONI = os.path.join(cartella_programma(), "scriba_settings.json")
CARTELLA_DIARI = os.path.join(cartella_programma(), "diari")
MODELLO_PRESET = {
    "titolo": "",
    "machine_id": "",
    "giorni_periodicita": 365,
    "ultimo_backup": None,
    "root_destinazione": "",
    "coppie_cartelle": [],
    "esclusioni": [],
    "storico_stats": {},
}

# --- DIARIO DI SESSIONE ---


class _Sdoppiatore:
    """Manda quel che Scriba scrive sia a schermo sia sul diario di sessione.
    Serve per poter rileggere una sessione intera, o consegnarla a chi analizza
    un problema, senza doverla ricopiare a mano dal terminale.
    """

    def __init__(self, flusso, apertura):
        self._flusso = flusso
        self._handle = apertura

    def write(self, testo: str) -> int:
        scritti = self._flusso.write(testo)
        with contextlib.suppress(OSError, ValueError):
            self._handle.write(testo)
        return scritti

    def flush(self) -> None:
        self._flusso.flush()
        with contextlib.suppress(OSError, ValueError):
            self._handle.flush()

    def isatty(self) -> bool:
        return getattr(self._flusso, "isatty", lambda: False)()


_diario_handle = None
_diario_path = None
_stdout_originale = None


def apri_diario() -> str | None:
    """Apre il diario della sessione e ci dirotta una copia di tutto l'output.
    Restituisce il percorso del file, oppure None se non è stato possibile
    aprirlo: in quel caso Scriba lavora comunque, soltanto senza diario.
    """
    global _diario_handle, _diario_path, _stdout_originale
    if _diario_handle is not None:
        return _diario_path
    try:
        os.makedirs(CARTELLA_DIARI, exist_ok=True)
        nome = datetime.datetime.now().strftime("sessione_%Y-%m-%d_%H-%M-%S.txt")
        percorso = os.path.join(CARTELLA_DIARI, nome)
        # Il diario resta aperto per tutta la sessione, quindi non puo'
        # stare dentro un gestore di contesto: da qui la deroga a SIM115.
        apertura = open(percorso, "w", encoding="utf-8", errors="replace")  # noqa: SIM115
    except OSError:
        return None
    _diario_handle = apertura
    _diario_path = percorso
    _stdout_originale = sys.stdout
    sys.stdout = _Sdoppiatore(_stdout_originale, apertura)
    intestazione = (
        f"Diario di {APP_NAME} v{APP_VERSION} del {RELEASE_DATE}\n"
        f"Avvio: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Macchina: {id_macchina()}\n"
        f"Eseguito da: {'eseguibile' if getattr(sys, 'frozen', False) else 'sorgente'}\n"
    )
    try:
        apertura.write(intestazione)
        apertura.flush()
    except (OSError, ValueError):
        pass
    return percorso


def chiudi_diario() -> None:
    """Chiude il diario e rimette a posto l'output normale."""
    global _diario_handle, _stdout_originale
    if _diario_handle is None:
        return
    try:
        _diario_handle.write(f"Chiusura: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        _diario_handle.close()
    except (OSError, ValueError):
        pass
    if _stdout_originale is not None:
        sys.stdout = _stdout_originale
    _diario_handle = None
    _stdout_originale = None


def annota(testo: str) -> None:
    """Scrive nel diario qualcosa che a schermo non serve mostrare."""
    if _diario_handle is None:
        return
    try:
        _diario_handle.write(testo if testo.endswith("\n") else testo + "\n")
        _diario_handle.flush()
    except (OSError, ValueError):
        pass


def chiedi(prompt: str = "") -> str:
    """Legge una risposta dalla tastiera e la annota nel diario.
    L'eco di quel che si digita lo fa il terminale, non Python, quindi senza
    questo passaggio il diario conserverebbe le domande e non le risposte.
    """
    risposta = input(prompt)
    annota(f"[risposta] {risposta}")
    return risposta


def conferma(domanda: str, guida: str = "Invio per confermare, Escape per annullare") -> bool:
    """Chiede una conferma con invio o escape, e la annota nel diario.
    Le domande che portano via dati non passano di qui: quelle chiedono di
    scrivere SI per esteso, perché un tasto solo è troppo poco.
    """
    esito = bool(enter_escape(domanda, guida))
    annota(f"[risposta] {domanda} -> {'si' if esito else 'no'}")
    return esito


def chiedi_numero(prompt: str, minimo: int, massimo: int, predefinito: int) -> int:
    """Chiede un numero intero con dgt, e lo annota nel diario."""
    valore = dgt(prompt, kind="i", imin=minimo, imax=massimo, default=predefinito)
    annota(f"[risposta] {valore}")
    return valore


def pausa(messaggio: str = "Premi invio per tornare al menu") -> None:
    """Aspetta un invio, e lo annota nel diario."""
    enter_escape(messaggio, guida="Premi invio, oppure escape")
    annota(f"[pausa] {messaggio}")


def _chiave_di_menu(etichetta: str, gia_usate) -> str:
    """Ricava dalla etichetta una chiave di menu che non sia gia' presa."""
    base = " ".join(str(etichetta).split()) or "voce"
    chiave = base
    prese = {k.lower() for k in gia_usate}
    progressivo = 2
    while chiave.lower() in prese:
        chiave = f"{base} {progressivo}"
        progressivo += 1
    return chiave


def scegli_voce(voci: list[tuple[str, str]], prompt: str = "Scegli") -> int | None:
    """Fa scegliere una voce da un elenco, con il menu a parole di GBUtils.
    Ogni voce e' una coppia, cioe' il nome da scrivere e la descrizione da
    leggere. Restituisce la posizione della voce scelta, oppure None se si
    annulla con il punto, con invio a vuoto o con escape.
    Si scrive il nome, o le prime lettere che bastano a distinguerlo: i
    numeri da contare nell'elenco non servono piu' a nessuno, e sbagliarne
    uno significava lavorare sul preset sbagliato.
    """
    if not voci:
        return None
    elenco = {}
    posizioni = {}
    for posizione, (etichetta, descrizione) in enumerate(voci):
        chiave = _chiave_di_menu(etichetta, elenco)
        elenco[chiave] = descrizione
        posizioni[chiave] = posizione
    elenco["."] = "Annulla"
    scelta = menu(elenco, show=True, keyslist=True, ordered=False, p=f"{prompt}: ")
    annota(f"[scelta] {prompt}: {scelta}")
    if scelta is None or scelta == ".":
        return None
    return posizioni.get(scelta)


# --- GESTIONE DATI E SICUREZZA ---


def id_macchina() -> str:
    """Nome della macchina e dell'utente, come li vede il sistema."""
    nome_macchina = platform.node()
    try:
        nome_utente = os.getlogin()
    except OSError:
        nome_utente = os.environ.get("USERNAME", "sconosciuto")
    return f"{nome_macchina} | {nome_utente}"


def nome_destinazione(percorso: str, nomi_usati: list[str] | None = None) -> str:
    """Ricava il nome della cartella di destinazione da un percorso di origine.
    Il nome deve essere unico dentro il preset: con /MIR due origini che
    finiscono nella stessa cartella si cancellano i dati a vicenda, perché la
    seconda considera estraneo tutto quel che ha copiato la prima. Se il nome
    è già preso si antepone quello della cartella superiore, e se non basta
    si aggiunge un numero.
    """
    pulito = percorso.replace("/", "\\").rstrip("\\")
    base = os.path.basename(pulito)
    if not base:
        base = pulito.replace(":", "").replace("\\", "") or "Radice"
    presi = [n.lower() for n in (nomi_usati or [])]
    if base.lower() not in presi:
        return base
    genitore = os.path.basename(os.path.dirname(pulito))
    if genitore:
        candidato = f"{genitore}-{base}"
        if candidato.lower() not in presi:
            return candidato
    progressivo = 2
    while f"{base}-{progressivo}".lower() in presi:
        progressivo += 1
    return f"{base}-{progressivo}"


def nomi_duplicati(preset: dict) -> list[str]:
    """Restituisce i nomi di destinazione usati da più di una origine.
    Il confronto ignora maiuscole e minuscole, perché per Windows sono lo
    stesso nome, ma il nome restituito è quello scritto nel preset, che è
    quello che l'utente si aspetta di leggere.
    """
    quante = {}
    scrittura = {}
    for coppia in preset.get("coppie_cartelle", []):
        nome = str(coppia.get("nome_cartella", ""))
        chiave = nome.lower()
        quante[chiave] = quante.get(chiave, 0) + 1
        scrittura.setdefault(chiave, nome)
    return sorted(scrittura[c] for c, volte in quante.items() if volte > 1 and c)


def _intero_valido(valore, minimo: int, riserva: int) -> int:
    """Converte un valore in intero, tornando alla riserva se non si può."""
    try:
        numero = int(valore)
    except (TypeError, ValueError):
        return riserva
    return numero if numero >= minimo else riserva


def valida_preset(preset: dict, posizione: int) -> tuple[dict, list[str]]:
    """Completa un preset con i campi mancanti e ne corregge i tipi.
    Non scarta mai niente di recuperabile: un preset con vent'anni di storia
    dentro vale più della pulizia formale. Restituisce il preset sistemato e
    l'elenco degli avvisi da mostrare a chi lo sta caricando.
    """
    avvisi = []
    sistemato = copy.deepcopy(preset)
    for chiave, valore in MODELLO_PRESET.items():
        if chiave not in sistemato:
            sistemato[chiave] = copy.deepcopy(valore)
    titolo = str(sistemato.get("titolo") or "").strip()
    if not titolo:
        titolo = f"Preset senza titolo {posizione}"
        avvisi.append(f"Preset {posizione}: manca il titolo, chiamato {titolo}.")
    sistemato["titolo"] = titolo
    machine = str(sistemato.get("machine_id") or "").strip()
    if not machine:
        machine = "Sconosciuto"
        avvisi.append(f"{titolo}: manca l'ID macchina.")
    sistemato["machine_id"] = machine
    giorni = _intero_valido(sistemato.get("giorni_periodicita"), 1, 365)
    if giorni != sistemato.get("giorni_periodicita"):
        avvisi.append(f"{titolo}: periodicità non valida, portata a {giorni} giorni.")
    sistemato["giorni_periodicita"] = giorni
    ultimo = sistemato.get("ultimo_backup")
    if ultimo is not None:
        try:
            datetime.datetime.strptime(str(ultimo), "%Y-%m-%d")
        except ValueError:
            avvisi.append(f"{titolo}: data dell'ultimo backup illeggibile, azzerata.")
            ultimo = None
    sistemato["ultimo_backup"] = ultimo
    sistemato["root_destinazione"] = str(sistemato.get("root_destinazione") or "")
    coppie = []
    for coppia in sistemato.get("coppie_cartelle") or []:
        if not isinstance(coppia, dict):
            avvisi.append(f"{titolo}: scartata una voce di cartella illeggibile.")
            continue
        origine = str(coppia.get("origine") or "").strip()
        if not origine:
            avvisi.append(f"{titolo}: scartata una cartella senza origine.")
            continue
        nome = str(coppia.get("nome_cartella") or "").strip()
        if not nome:
            nome = nome_destinazione(origine, [c["nome_cartella"] for c in coppie])
            avvisi.append(f"{titolo}: cartella senza nome di destinazione, chiamata {nome}.")
        coppie.append({"origine": origine, "nome_cartella": nome})
    sistemato["coppie_cartelle"] = coppie
    sistemato["esclusioni"] = [str(e) for e in (sistemato.get("esclusioni") or []) if str(e).strip()]
    storico = sistemato.get("storico_stats")
    sistemato["storico_stats"] = storico if isinstance(storico, dict) else {}
    doppioni = nomi_duplicati(sistemato)
    if doppioni:
        avvisi.append(f"{titolo}: nomi di destinazione ripetuti, {', '.join(doppioni)}.")
        avvisi.append("Con /MIR la seconda copia cancella la prima, correggerli prima di eseguire.")
    return sistemato, avvisi


def valida_impostazioni(dati) -> tuple[dict, list[str]]:
    """Porta le impostazioni allo schema corrente e ne ripara i difetti.
    Il file può arrivare da una versione precedente, da un'altra macchina o
    da una modifica a mano: prima si controlla, poi lo si usa, altrimenti il
    primo campo mancante ferma il programma con un errore incomprensibile.
    """
    avvisi = []
    if not isinstance(dati, dict):
        return {"schema": VERSIONE_SCHEMA, "presets": []}, ["Impostazioni illeggibili, riparto da vuoto."]
    elenco = dati.get("presets")
    if not isinstance(elenco, list):
        if elenco is not None:
            avvisi.append("L'elenco dei preset era illeggibile, sostituito con uno vuoto.")
        elenco = []
    presets = []
    for posizione, preset in enumerate(elenco, start=1):
        if not isinstance(preset, dict):
            avvisi.append(f"Preset {posizione}: voce illeggibile, saltata.")
            continue
        sistemato, suoi_avvisi = valida_preset(preset, posizione)
        presets.append(sistemato)
        avvisi.extend(suoi_avvisi)
    schema = _intero_valido(dati.get("schema"), 0, 0)
    if schema > VERSIONE_SCHEMA:
        avvisi.append(f"Le impostazioni vengono da una versione più recente, schema {schema}.")
    return {"schema": VERSIONE_SCHEMA, "presets": presets}, avvisi


def carica_impostazioni() -> dict | None:
    """Carica le impostazioni, le valida e le porta allo schema corrente."""
    if not os.path.exists(FILE_IMPOSTAZIONI):
        return {"schema": VERSIONE_SCHEMA, "presets": []}
    try:
        with open(FILE_IMPOSTAZIONI, encoding="utf-8") as f:
            grezzo = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print("Impossibile leggere le impostazioni.")
        print(f"Motivo: {e}")
        print(f"File: {FILE_IMPOSTAZIONI}")
        if os.path.exists(FILE_IMPOSTAZIONI + ".bak"):
            print("Accanto al file c'è una copia .bak")
            print("della versione precedente.")
        return None
    dati, avvisi = valida_impostazioni(grezzo)
    for avviso in avvisi:
        print(avviso)
    return dati


def salva_impostazioni(data: dict | None) -> bool:
    """Salva le impostazioni senza mai lasciare il file a metà.
    Scrive prima un file temporaneo, lo rilegge per verificare che sia JSON
    valido e solo allora sostituisce l'originale, dopo averne messo da parte
    la copia .bak. Il metodo precedente copiava l'originale nel .bak prima di
    scrivere, quindi un salvataggio andato male e uno successivo bastavano a
    portarsi via anche l'unica copia buona.
    """
    if data is None:
        return False
    data["schema"] = VERSIONE_SCHEMA
    temporaneo = FILE_IMPOSTAZIONI + ".tmp"
    try:
        with open(temporaneo, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        with open(temporaneo, encoding="utf-8") as f:
            json.load(f)
    except (OSError, TypeError, ValueError) as e:
        print(f"Salvataggio non riuscito: {e}")
        with contextlib.suppress(OSError):
            os.remove(temporaneo)
        return False
    try:
        if os.path.exists(FILE_IMPOSTAZIONI):
            shutil.copy2(FILE_IMPOSTAZIONI, FILE_IMPOSTAZIONI + ".bak")
        os.replace(temporaneo, FILE_IMPOSTAZIONI)
    except OSError as e:
        print(f"Salvataggio non riuscito: {e}")
        return False
    return True


def percorso_lungo(percorso_scelto: str) -> str:
    if os.name == "nt" and len(percorso_scelto) > 0 and not percorso_scelto.startswith("\\\\?\\"):
        percorso_scelto = os.path.abspath(percorso_scelto)
        if percorso_scelto.startswith("\\\\"):
            return "\\\\?\\UNC\\" + percorso_scelto[2:]
        return "\\\\?\\" + percorso_scelto
    return percorso_scelto


# --- INTERFACCIA UTENTE E UTILITIES ---


def scegli_cartella(message: str = "Seleziona una cartella") -> str | None:
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    cartella_scelta = filedialog.askdirectory(title=message)
    root.destroy()
    return cartella_scelta if cartella_scelta else None


def accorcia(text: str, max_len: int = 45) -> str:
    if len(text) <= max_len:
        return text
    meta_lunghezza = (max_len - 3) // 2
    testa = text[:meta_lunghezza]
    coda = text[-meta_lunghezza:]
    return f"{testa}...{coda}"


def formatta_dimensione(byte: float) -> str:
    sign = ""
    if byte < 0:
        sign = "-"
        byte = abs(byte)
    if byte == 0:
        return "0.00 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if byte < 1024.0:
            return f"{sign}{byte:.2f} {unit}"
        byte /= 1024.0
    return f"{sign}{byte:.2f} PB"


def stampa_dettaglio_esteso(preset: dict) -> None:
    """Mostra il preset prima di eseguirlo, una riga per informazione."""
    print()
    print(f"Preset {preset['titolo']}")
    print(f"Macchina {preset['machine_id'] or 'sconosciuta'}")
    print(f"Periodicità ogni {preset['giorni_periodicita']} giorni")
    print(f"Ultima esecuzione {preset['ultimo_backup'] or 'mai'}")
    print(f"Destinazione {preset['root_destinazione']}")
    quante = len(preset["coppie_cartelle"])
    print(f"Cartelle da elaborare {quante}")
    da_mostrare = preset["coppie_cartelle"]
    if quante > 15:
        da_mostrare = preset["coppie_cartelle"][:5] + preset["coppie_cartelle"][-5:]
    for posto, c in enumerate(da_mostrare, start=1):
        if quante > 15 and posto == 6:
            print(f"altre {quante - 10} cartelle non elencate")
        print(f"{c['origine']} va in {c['nome_cartella']}")


def pulisci_log_vecchi(cartella_log: str, giorni_massimi: int = 30) -> None:
    """Toglie dalla cartella i file di testo più vecchi di tanti giorni.
    Serve sia per i log di robocopy nella destinazione sia per i diari di
    sessione. I guai finiscono nel diario e non a schermo: è pulizia di
    contorno, non deve rubare l'attenzione a un backup in corso.
    """
    if not os.path.exists(cartella_log):
        return
    limite = time.time() - (giorni_massimi * 86400)
    try:
        elenco = os.listdir(cartella_log)
    except OSError as e:
        annota(f"[pulizia] cartella {cartella_log} non leggibile: {e}")
        return
    for nome in elenco:
        if not nome.endswith(".txt"):
            continue
        percorso = os.path.join(cartella_log, nome)
        try:
            if os.path.isfile(percorso) and os.path.getmtime(percorso) < limite:
                os.remove(percorso)
        except OSError as e:
            annota(f"[pulizia] {percorso} non rimosso: {e}")


def _suona(score: list, kind: int = ONDA, adsr: list | None = None) -> None:
    """Manda uno score ad Acusticator senza che un guaio audio fermi il backup.
    La cattura è larga di proposito: qualunque cosa succeda alla scheda audio,
    a un dispositivo staccato o a un driver capriccioso, la copia dei dati
    deve proseguire. Il guaio finisce nel diario, non a schermo.
    """
    try:
        Acusticator(score, kind=kind, adsr=adsr, sync=False)
    except Exception as e:  # noqa: BLE001
        annota(f"[audio] segnale non riprodotto: {e}")


def scalda_audio() -> None:
    """Apre lo stream audio prima che serva davvero.
    Misurato: la prima chiamata ad Acusticator costa circa 840 millisecondi,
    perché carica il motore e apre la scheda; tutte le successive stanno
    sotto i 5. Pagare quel conto durante la copia farebbe inciampare la
    lettura del log, quindi lo si paga all'avvio, in un thread a parte, con
    una pausa muta.
    """

    def lavoro():
        _suona(["p", 0.01, 0, 0])

    threading.Thread(target=lavoro, daemon=True).start()


def nota_avanzamento(frazione: float) -> str:
    """Traduce una frazione di avanzamento, da 0 a 1, in una nota da c2 a b8.
    Sette ottave piene: il suono sale insieme al backup e dice a orecchio a
    che punto siamo, senza che nessuno debba leggere niente.
    """
    frazione = max(0.0, min(1.0, frazione))
    semitoni = round(frazione * (len(SCALA) * OTTAVE - 1))
    return f"{SCALA[semitoni % len(SCALA)]}{OTTAVA_MINIMA + semitoni // len(SCALA)}"


def suona_avanzamento(frazione: float) -> None:
    """Segnale breve di avanzamento: nota e posizione stereo salgono insieme.
    A zero per cento suona un do grave tutto a sinistra, a cento un si acuto
    tutto a destra.
    """
    frazione = max(0.0, min(1.0, frazione))
    pan = -1.0 + 2.0 * frazione
    _suona([nota_avanzamento(frazione), DURATA_SEGNALE, round(pan, 3), VOLUME_SEGNALE], adsr=ADSR_SEGNALE)


def suona_esito(riuscito: bool = True) -> None:
    """Chiude la sessione con un accordo che sale se è andata bene e scende se no."""
    if riuscito:
        _suona(["c6", 0.09, -0.5, 0.5, "e6", 0.09, 0, 0.5, "g6", 0.16, 0.5, 0.5], adsr=ADSR_SEGNALE)
    else:
        _suona(["g3", 0.12, 0.5, 0.5, "d#3", 0.12, 0, 0.5, "c3", 0.22, -0.5, 0.5], adsr=ADSR_SEGNALE)


# --- COMANDI E LETTURA DI ROBOCOPY ---

# I flag che decidono quali file toccare stanno qui, in un elenco solo, ed
# è la ragione per cui ci stanno: l'analisi girava senza /FFT e la copia con,
# quindi le due passate non guardavano gli stessi file. Verso una condivisione
# di rete, dove gli orari ballano di un secondo, quella differenza da sola
# bastava a promettere molti più byte di quelli che poi venivano trasferiti.
FLAG_SELEZIONE = ["/MIR", "/XJ", "/R:1", "/W:1", "/FFT", "/BYTES"]
CARTELLE_ESCLUSE = ["$RECYCLE.BIN", "System Volume Information"]
FILE_ESCLUSI = [
    "pagefile.sys",
    "hiberfil.sys",
    "swapfile.sys",
    "parent.lock",
    "*.lock",
    "*.parentlock",
    "*.gsheet",
    "*.gdoc",
    "*.gslides",
    "*.gdraw",
    "*.gtable",
    "*.glink",
    "*.gform",
    "*.gmap",
]


def _senza_prefisso_lungo(percorso: str) -> str:
    """Toglie il prefisso dei percorsi lunghi, che robocopy non vuole."""
    pulito = percorso.replace("\\\\?\\UNC\\", "\\\\").replace("\\\\?\\", "")
    if pulito.endswith("\\") and not pulito.endswith(":\\"):
        pulito = pulito.rstrip("\\")
    return pulito


def comando_robocopy(
    origine_piena: str,
    destinazione_piena: str,
    esclusioni: list[str] | None = None,
    simulazione: bool = False,
    piano: bool = False,
) -> list[str]:
    """Costruisce il comando robocopy.
    Fra la passata di analisi e la copia vera cambia soltanto /L, che elenca
    senza copiare, e quanto robocopy scrive nel log. Tutto quello che
    influenza la scelta dei file viene da FLAG_SELEZIONE, uguale per
    entrambe, così le due passate rispondono alla stessa domanda.
    """
    comando = ["robocopy", _senza_prefisso_lungo(origine_piena), _senza_prefisso_lungo(destinazione_piena)]
    comando += list(FLAG_SELEZIONE)
    if piano:
        comando += ["/L", "/NJH", "/NJS", "/NDL"]
    elif simulazione:
        comando.append("/L")
    escluse = list(CARTELLE_ESCLUSE)
    _, coda = os.path.splitdrive(origine_piena)
    if coda in ["\\", "/", ""] or origine_piena.endswith(":\\"):
        escluse.append("Recovery")
    if esclusioni:
        escluse.extend(_senza_prefisso_lungo(e) for e in esclusioni)
    comando.extend(["/XD", *escluse])
    comando.extend(["/XF", *FILE_ESCLUSI])
    return comando


def analizza_riga_robocopy(riga: str) -> tuple[str, int, str] | None:
    """Riconosce una riga di file di robocopy dalla struttura, non dalla lingua.
    Robocopy separa i campi con tabulazioni e scrive tag, dimensione e nome:
    tre campi, con la dimensione tutta di cifre. Le righe di cartella ne
    hanno due, perché tag e conteggio stanno insieme.
    Restituisce il tipo, i byte e il nome, oppure None se non è una riga di
    file. Il tipo è copia per i file da trasferire ed extra per quelli che
    il mirror cancellerà sulla destinazione.
    Prima il riconoscimento cercava sottostringhe in qualunque punto della
    riga, e fra queste new e newer minuscoli: bastava un percorso che le
    contenesse, da renewal a news.json, perché una cancellazione venisse
    contata come byte da trasferire.
    """
    campi = [c.strip() for c in riga.split("\t")]
    campi = [c for c in campi if c]
    if len(campi) != 3:
        return None
    tag, dimensione, nome = campi
    if not dimensione.isdigit() or not nome:
        return None
    return ("extra" if tag.startswith("*") else "copia"), int(dimensione), nome


def analizza_sommario(righe: list[str]) -> dict[str, int]:
    """Legge le tre righe di totali che robocopy scrive in fondo al log.
    Sono nell'ordine cartelle, file e byte, ciascuna con sei numeri: totale,
    copiati, ignorati, non corrispondenti, non riusciti, supplementari. Si
    riconoscono dalla forma, cioè un'etichetta senza cifre e poi sei numeri
    interi e nient'altro, così la lingua di Windows non conta e la riga dei
    tempi, che ha gli orari con i due punti, non viene scambiata per loro.
    """
    vuoto = {
        "dirs_total": 0,
        "dirs_copied": 0,
        "dirs_skipped": 0,
        "dirs_failed": 0,
        "files_total": 0,
        "files_copied": 0,
        "files_skipped": 0,
        "files_failed": 0,
        "bytes_total": 0,
        "bytes_copied": 0,
        "bytes_skipped": 0,
        "bytes_failed": 0,
    }
    trovate = []
    for riga in righe:
        if ":" not in riga:
            continue
        etichetta, resto = riga.split(":", 1)
        if any(c.isdigit() for c in etichetta):
            continue
        pezzi = resto.split()
        if len(pezzi) != 6 or not all(p.isdigit() for p in pezzi):
            continue
        trovate.append([int(p) for p in pezzi])
    if len(trovate) < 3:
        return vuoto
    cartelle, file_, byte_ = trovate[0], trovate[1], trovate[2]
    return {
        "dirs_total": cartelle[0],
        "dirs_copied": cartelle[1],
        "dirs_skipped": cartelle[2],
        "dirs_failed": cartelle[4],
        "files_total": file_[0],
        "files_copied": file_[1],
        "files_skipped": file_[2],
        "files_failed": file_[4],
        "bytes_total": byte_[0],
        "bytes_copied": byte_[1],
        "bytes_skipped": byte_[2],
        "bytes_failed": byte_[4],
    }


def analizza_errore_robocopy(line: str) -> dict | None:
    """Estrae numero, codice e dettaglio da una riga di errore di robocopy."""
    trovato = re.search(r"(?:ERROR|ERRORE)\s+(\d+)\s+\((0x[0-9a-fA-F]+)\)(?:\s+(.*))?", line)
    if not trovato:
        return None
    return {
        "code_dec": int(trovato.group(1)),
        "code_hex": trovato.group(2),
        "detail": (trovato.group(3) or "Dettagli non disponibili").strip(),
    }


def formatta_durata(seconds: float) -> str:
    """Formatta un numero di secondi come durata compatta."""
    if seconds is None or seconds <= 0:
        return "--:--"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"


def blocchi(*pezzi) -> str:
    """Compone una riga in blocchi di quaranta caratteri.
    Un display braille da quaranta celle ne mostra uno per volta: se ogni
    informazione sta dentro il suo, si legge scorrendo di un passo e non si
    trova mai un dato spezzato a metà.
    """
    fuori = []
    for pezzo in pezzi:
        testo = str(pezzo)
        fuori.append(testo if len(testo) > LARGHEZZA_BLOCCO else testo.ljust(LARGHEZZA_BLOCCO))
    return "".join(fuori).rstrip()


def tasto_premuto() -> bool:
    """Dice se qualcuno ha premuto un tasto, e svuota quel che ha digitato.
    Serve per lo stato a richiesta durante la copia: nessuna riga che si
    riscrive da sola, si parla soltanto quando lo si chiede.
    """
    if msvcrt is None:
        return False
    premuto = False
    while msvcrt.kbhit():
        msvcrt.getwch()
        premuto = True
    return premuto


class Stimatore:
    """Dice a che punto siamo e quanto manca.
    Il tempo si stima con il tempo. In un backup periodico quasi tutto il
    tempo se ne va a confrontare cartelle in cui non è cambiato niente, che
    di byte ne valgono zero: una percentuale calcolata sui byte resta ferma
    per minuti e poi salta, e la stima non converge mai. La sessione
    precedente invece è un oracolo molto migliore, perché l'archivio cambia
    poco e le stesse cartelle costano più o meno lo stesso tempo. Da lì si
    parte, e si corregge in corsa con il rapporto fra il tempo speso davvero
    e quello che ci si aspettava.
    Alla prima esecuzione di un preset la storia non c'è ancora e si ripiega
    sui byte previsti dall'analisi, misurando la velocità su una finestra
    recente invece che sulla media dall'inizio, che reagisce troppo tardi.
    Senza né storia né analisi non si inventa niente: si contano le cartelle
    fatte e i byte copiati, e la stima si dichiara non disponibile.
    """

    FINESTRA_VELOCITA = 30.0
    CORREZIONE_MINIMA = 0.2
    CORREZIONE_MASSIMA = 5.0

    def __init__(self, cartelle: list[str], storico: dict | None = None, byte_previsti: dict | None = None):
        self.cartelle = list(cartelle)
        self.attesa = {}
        self.byte_previsti = dict(byte_previsti or {})
        durate = []
        for nome in self.cartelle:
            voce = (storico or {}).get(nome)
            if isinstance(voce, dict) and voce.get("durata", 0) > 0:
                durate.append(float(voce["durata"]))
        media = sum(durate) / len(durate) if durate else 0.0
        for nome in self.cartelle:
            voce = (storico or {}).get(nome)
            if isinstance(voce, dict) and voce.get("durata", 0) > 0:
                self.attesa[nome] = float(voce["durata"])
            else:
                self.attesa[nome] = media
        self.totale_atteso = sum(self.attesa.values())
        self.totale_byte = sum(self.byte_previsti.values())
        if durate and self.totale_atteso > 0:
            self.modo = "tempo"
        elif self.totale_byte > 0:
            self.modo = "byte"
        else:
            self.modo = "nessuna"
        self.fatte = []
        self.speso = 0.0
        self.corrente = None
        self.byte_fatti = 0
        self._campioni = []

    def inizia(self, nome: str) -> None:
        self.corrente = nome

    def concludi(self, nome: str, durata: float, byte_copiati: int) -> None:
        if nome not in self.fatte:
            self.fatte.append(nome)
        self.speso += max(0.0, durata)
        self.byte_fatti += max(0, byte_copiati)
        self.corrente = None

    def aggiorna_byte(self, byte_nella_cartella: int) -> None:
        """Registra i byte fatti nella cartella in corso, per la velocità."""
        adesso = time.time()
        self._campioni.append((adesso, self.byte_fatti + max(0, byte_nella_cartella)))
        taglio = adesso - self.FINESTRA_VELOCITA
        while len(self._campioni) > 2 and self._campioni[0][0] < taglio:
            self._campioni.pop(0)

    @property
    def fattore(self) -> float:
        """Quanto questa sessione sta andando più lenta o più svelta della scorsa."""
        atteso = sum(self.attesa.get(n, 0.0) for n in self.fatte)
        if self.modo != "tempo" or atteso <= 0:
            return 1.0
        return max(self.CORREZIONE_MINIMA, min(self.CORREZIONE_MASSIMA, self.speso / atteso))

    def _velocita(self) -> float:
        """Byte al secondo sulla finestra recente, zero se non si sa ancora."""
        if len(self._campioni) < 2:
            return 0.0
        (t0, b0), (t1, b1) = self._campioni[0], self._campioni[-1]
        return (b1 - b0) / (t1 - t0) if t1 > t0 and b1 > b0 else 0.0

    def frazione(self, trascorso: float = 0.0, byte_nella_cartella: int = 0) -> float:
        """Avanzamento complessivo da 0 a 1. Un numero c'è sempre.
        Quando non c'è né storia né analisi, per esempio in una simulazione,
        si contano le cartelle concluse: è meno preciso ma è vero, e
        soprattutto è qualcosa. Prima in quel caso non si restituiva niente,
        e siccome il suono dell'avanzamento dipende da questo numero, una
        simulazione lunga si svolgeva in totale silenzio.
        """
        if self.modo == "tempo":
            fatto = sum(self.attesa.get(n, 0.0) for n in self.fatte)
            if self.corrente:
                attesa_corrente = self.attesa.get(self.corrente, 0.0)
                previsto = attesa_corrente * self.fattore
                if previsto > 0:
                    fatto += min(1.0, trascorso / previsto) * attesa_corrente
            return max(0.0, min(1.0, fatto / self.totale_atteso))
        if self.modo == "byte":
            fatti = self.byte_fatti + max(0, byte_nella_cartella)
            return max(0.0, min(1.0, fatti / self.totale_byte))
        if not self.cartelle:
            return 0.0
        fatte = len(self.fatte) + (0.5 if self.corrente else 0.0)
        return max(0.0, min(1.0, fatte / len(self.cartelle)))

    def frazione_cartella(self, trascorso: float = 0.0, byte_nella_cartella: int = 0) -> float:
        """Avanzamento della sola cartella in corso, da 0 a 1.
        Serve alla barra: sapere che il totale è al dodici per cento non dice
        se la cartella che si sta copiando adesso è appena cominciata o quasi
        finita, e quando una cartella dura mezz'ora quella è l'informazione
        che si aspetta.
        """
        if not self.corrente:
            return 0.0
        if self.modo == "tempo":
            previsto = self.attesa.get(self.corrente, 0.0) * self.fattore
            return min(1.0, trascorso / previsto) if previsto > 0 else 0.0
        previsti = self.byte_previsti.get(self.corrente, 0)
        if previsti > 0:
            return max(0.0, min(1.0, byte_nella_cartella / previsti))
        return 0.0

    def eta(self, trascorso: float = 0.0, byte_nella_cartella: int = 0) -> float | None:
        """Secondi che mancano, None se non è stimabile."""
        if self.modo == "tempo":
            k = self.fattore
            residuo = 0.0
            for nome in self.cartelle:
                if nome in self.fatte or nome == self.corrente:
                    continue
                residuo += self.attesa.get(nome, 0.0) * k
            if self.corrente:
                residuo += max(0.0, self.attesa.get(self.corrente, 0.0) * k - trascorso)
            return residuo
        if self.modo == "byte":
            velocita = self._velocita()
            if velocita <= 0:
                return None
            mancano = self.totale_byte - (self.byte_fatti + max(0, byte_nella_cartella))
            return max(0.0, mancano / velocita)
        return None

    def previsione_iniziale(self) -> str:
        """Una frase sola da dire prima di cominciare."""
        if self.modo == "tempo":
            if self.totale_atteso < 60:
                return "Previsto meno di un minuto, dalla volta scorsa."
            return f"Previsti {formatta_durata(self.totale_atteso)}, dalla volta scorsa."
        if self.modo == "byte":
            return f"Da trasferire {formatta_dimensione(self.totale_byte)}."
        return "Prima esecuzione, nessuna previsione."


def conta_da_trasferire(
    origine_piena: str,
    destinazione_piena: str,
    esclusioni: list[str] | None = None,
    nome: str = "",
    indice: int = 1,
    totale: int = 1,
) -> tuple[int, int]:
    """Passata di sola lettura che conta quanti file e quanti byte servono.
    Serve soltanto alla prima esecuzione di un preset su una macchina, quando
    non c'è ancora una sessione precedente da cui stimare i tempi: dalla
    seconda in poi la si salta, e con lei si risparmia una scansione intera
    di origine e destinazione.
    Restituisce file e byte da copiare, oppure None se la passata è fallita:
    zero e zero vorrebbe dire che non c'è niente da fare, che è tutt'altra
    cosa.
    """
    comando = comando_robocopy(origine_piena, destinazione_piena, esclusioni, simulazione=True, piano=True)
    file_da_copiare = 0
    byte_da_copiare = 0
    try:
        avvio_nascosto = subprocess.STARTUPINFO()
        avvio_nascosto.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        processo = subprocess.Popen(
            comando,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            encoding=CODIFICA_LOG,
            errors="replace",
            startupinfo=avvio_nascosto,
        )
        ultima_barra = 0.0
        for riga in processo.stdout:
            letta = analizza_riga_robocopy(riga.rstrip("\r\n"))
            if letta and letta[0] == "copia":
                byte_da_copiare += letta[1]
                file_da_copiare += 1
            adesso = time.time()
            if adesso - ultima_barra >= SECONDI_FRA_AGGIORNAMENTI:
                # Anche l'analisi ha la sua barra: su una destinazione di rete
                # una sola cartella puo' tenere occupato qualche minuto, e
                # restare senza niente da leggere in quel tratto e' proprio il
                # difetto che si sta togliendo.
                testa = f"analisi {indice}/{totale} {file_da_copiare} file {formatta_dimensione(byte_da_copiare)} "
                spazio = LARGHEZZA_BLOCCO - len(testa)
                if spazio > 2:
                    testa += accorcia(nome, spazio)
                mostra_barra(testa.ljust(LARGHEZZA_BLOCCO)[:LARGHEZZA_BLOCCO])
                ultima_barra = adesso
        processo.wait()
    except (OSError, ValueError) as e:
        annota(f"[analisi] passata fallita su {origine_piena}: {e}")
        return None
    return file_da_copiare, byte_da_copiare


def _leggi_log_dal_vivo(file_log: str, processo: subprocess.Popen, attesa: float = 5.0):
    """Legge il log di robocopy mentre viene scritto, a blocchi.
    Robocopy separa le percentuali di avanzamento con il ritorno di
    carrello, quindi si spezza sia sul ritorno a capo sia su quello.
    Prima si leggeva un carattere alla volta: misurato, quel modo regge 4,9
    milioni di caratteri al secondo e quindi non era il collo di bottiglia,
    ma leggere a blocchi costa 57 volte meno e non c'era motivo di no.
    Quando non arriva niente restituisce None ogni SECONDI_FRA_BATTITI: è il
    battito che tiene sveglio chi legge. Senza, mentre robocopy percorre un
    albero grosso senza copiare nulla, il ciclo resterebbe fermo dentro la
    lettura e non potrebbe né rispondere a un tasto né dire che è ancora
    vivo, che è proprio il tratto in cui l'attesa sembra infinita.
    """
    inizio = time.time()
    while not os.path.exists(file_log) and processo.poll() is None:
        if time.time() - inizio > attesa:
            break
        time.sleep(0.05)
    if not os.path.exists(file_log):
        return
    resto = ""
    ultimo_battito = time.time()
    with open(file_log, encoding=CODIFICA_LOG, errors="replace") as f:
        while True:
            blocco = f.read(65536)
            if not blocco:
                if processo.poll() is None:
                    time.sleep(0.05)
                    if time.time() - ultimo_battito >= SECONDI_FRA_BATTITI:
                        ultimo_battito = time.time()
                        yield None
                    continue
                blocco = f.read(65536)
                if not blocco:
                    if resto.strip():
                        yield resto
                    return
            ultimo_battito = time.time()
            resto += blocco
            pezzi = re.split(r"[\r\n]", resto)
            resto = pezzi.pop()
            for pezzo in pezzi:
                if pezzo.strip():
                    yield pezzo


def esegui_robocopy(
    origine_piena: str,
    destinazione_piena: str,
    file_log: str,
    esclusioni: list[str] | None = None,
    simulazione: bool = False,
    stimatore: Stimatore | None = None,
    nome_cartella: str = "",
    indice: int = 1,
    totale: int = 1,
) -> tuple[dict[str, int], int, list[dict], int]:
    """Esegue robocopy su una coppia e ne segue l'avanzamento dal log.
    Durante la copia non stampa niente per conto proprio: una riga di stato
    esce solo se qualcuno preme un tasto, oppure ogni SECONDI_FRA_RIGHE se
    quella cadenza è accesa. L'avanzamento lo racconta il suono.
    Restituisce le statistiche finali, i byte trasferiti, gli errori e il
    numero di file che il mirror ha cancellato sulla destinazione.
    """
    comando = comando_robocopy(origine_piena, destinazione_piena, esclusioni, simulazione, piano=False)
    avvio_nascosto = subprocess.STARTUPINFO()
    avvio_nascosto.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    errori = []
    byte_fatti = 0
    file_cancellati = 0
    codice = -1
    processo = None
    log_al_lavoro = ""
    dimensione_corrente = 0
    frazione_file = 0.0
    ultima_riga = time.time()
    ultima_barra = 0.0
    ultimo_segnale = 0.0
    prossimo_segnale = 0.0
    inizio = time.time()
    sommario = []
    regola_percentuale = re.compile(r"^\s*(\d+[.,]?\d*)%\s*$")
    statistiche = analizza_sommario([])

    def fatti_adesso():
        return byte_fatti + int(dimensione_corrente * frazione_file)

    def aggiorna_barra(forza=False):
        """Riscrive la barra a schermo, e ogni tanto una riga nel diario."""
        nonlocal ultima_barra, ultima_riga
        adesso = time.time()
        if forza or adesso - ultima_barra >= SECONDI_FRA_AGGIORNAMENTI:
            mostra_barra(
                testo_barra(nome_cartella, indice, totale, stimatore, adesso - inizio, fatti_adesso())
            )
            ultima_barra = adesso
        if SECONDI_FRA_RIGHE > 0 and adesso - ultima_riga >= SECONDI_FRA_RIGHE:
            annota(
                f"[stato] {testo_barra(nome_cartella, indice, totale, stimatore, adesso - inizio, fatti_adesso())}"
            )
            ultima_riga = adesso

    def forse_suona():
        nonlocal ultimo_segnale, prossimo_segnale
        if stimatore is None:
            return
        adesso = time.time()
        if adesso - ultimo_segnale < SECONDI_FRA_SEGNALI:
            return
        frazione = stimatore.frazione(adesso - inizio, fatti_adesso())
        if frazione < prossimo_segnale:
            return
        suona_avanzamento(frazione)
        ultimo_segnale = adesso
        prossimo_segnale = frazione + PASSO_SEGNALE

    # La barra si disegna subito, prima ancora di lanciare robocopy: fra la
    # riga della cartella appena conclusa e il primo dato della successiva
    # passa il tempo di avviare il processo e di veder comparire il log, e in
    # quel tratto la barra sparirebbe. Con le cartelle che si concludono in un
    # decimo di secondo, come succede quando non c'e' niente da copiare, quel
    # buco si ripete di continuo ed e' la barra che sembra ballare.
    aggiorna_barra(forza=True)
    try:
        # Il log si scrive in locale e si sposta a destinazione alla fine.
        # Leggere dal vivo un file che un altro processo sta scrivendo su una
        # condivisione di rete e' fragile: sul campo, dopo dodici minuti, una
        # lettura e' fallita con Errno 22 e le statistiche di quella cartella
        # sono andate perse. In locale quel rischio non c'e'.
        log_al_lavoro = os.path.join(tempfile.gettempdir(), os.path.basename(file_log))
        with open(log_al_lavoro, "w", encoding=CODIFICA_LOG, errors="replace") as f_log:
            f_log.write(f"Avvio {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n")
            f_log.write(f"Origine {origine_piena}\nDestinazione {destinazione_piena}\n")
        comando.append(f"/LOG+:{log_al_lavoro}")
        processo = subprocess.Popen(
            comando, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=avvio_nascosto
        )
        for segmento in _leggi_log_dal_vivo(log_al_lavoro, processo):
            if segmento is None:
                # Battito: robocopy sta lavorando in silenzio, ma il tempo
                # passa lo stesso e la barra deve continuare a dirlo.
                forse_suona()
                aggiorna_barra()
                continue
            testo = segmento.strip()
            if not testo:
                continue
            percentuale = regola_percentuale.match(testo)
            if percentuale:
                frazione_file = float(percentuale.group(1).replace(",", ".")) / 100.0
                if stimatore is not None:
                    stimatore.aggiorna_byte(byte_fatti + int(dimensione_corrente * frazione_file))
                forse_suona()
                aggiorna_barra()
                continue
            if set(testo) == {"-"}:
                sommario = []
                continue
            if "ERROR" in testo or "ERRORE" in testo:
                errore = analizza_errore_robocopy(testo)
                if errore:
                    errori.append(errore)
                sommario.append(testo)
                continue
            letta = analizza_riga_robocopy(segmento)
            if letta:
                tipo, dimensione, _nome = letta
                if tipo == "extra":
                    file_cancellati += 1
                else:
                    byte_fatti += dimensione_corrente
                    dimensione_corrente = dimensione
                    frazione_file = 0.0
                    if stimatore is not None:
                        stimatore.aggiorna_byte(byte_fatti)
                    forse_suona()
                aggiorna_barra()
                continue
            sommario.append(testo)

        byte_fatti += dimensione_corrente
        dimensione_corrente = 0
        frazione_file = 0.0
    except OSError as e:
        # La lettura si e' rotta, ma robocopy sta ancora lavorando: non lo si
        # abbandona. Si aspetta comunque, e le statistiche si recuperano dal
        # log a lavoro concluso.
        print("La lettura dell'avanzamento si e' interrotta,")
        print("la copia prosegue lo stesso.")
        annota(f"[motore] lettura interrotta su {origine_piena}: {e}")

    if processo is not None:
        try:
            processo.wait()
            codice = processo.returncode
        except OSError as e:
            annota(f"[motore] attesa di robocopy non riuscita: {e}")
    if log_al_lavoro:
        dal_file = _sommario_dal_log(log_al_lavoro)
        if dal_file:
            statistiche = dal_file
        elif sommario:
            statistiche = analizza_sommario(sommario)
        _porta_a_destinazione(log_al_lavoro, file_log)
    annota(f"[robocopy] {nome_cartella}: codice di uscita {codice}, {descrivi_codice_robocopy(codice)}")
    if codice >= 8:
        print(f"Robocopy ha segnalato un problema su {nome_cartella}:")
        print(descrivi_codice_robocopy(codice))

    return statistiche, byte_fatti, errori, file_cancellati, codice


def _sommario_dal_log(percorso: str) -> dict[str, int] | None:
    """Rilegge il sommario dal log a copia conclusa.
    Le statistiche non devono dipendere dall'essere riusciti a leggere il log
    mentre veniva scritto: a lavoro finito il file e' li', completo e fermo,
    ed e' la fonte piu' affidabile che ci sia.
    """
    try:
        with open(percorso, encoding=CODIFICA_LOG, errors="replace") as f:
            righe = f.read().splitlines()
    except OSError as e:
        annota(f"[motore] sommario non rileggibile da {percorso}: {e}")
        return None
    coda = []
    for riga in righe:
        if riga.strip() and set(riga.strip()) == {"-"}:
            coda = []
            continue
        coda.append(riga)
    letto = analizza_sommario(coda)
    return letto if letto["files_total"] or letto["bytes_total"] or letto["dirs_total"] else None


def _porta_a_destinazione(sorgente: str, arrivo: str) -> None:
    """Sposta il log finito accanto agli altri, nella destinazione."""
    try:
        os.makedirs(os.path.dirname(arrivo), exist_ok=True)
        shutil.move(sorgente, arrivo)
    except OSError as e:
        annota(f"[motore] log rimasto in {sorgente}: {e}")


def descrivi_codice_robocopy(codice: int) -> str:
    """Traduce il codice di uscita di robocopy in una frase.
    Il codice e' fatto di bit che si sommano, e distingue il caso in cui non
    c'era niente da fare da quello in cui qualcosa e' andato storto. Senza
    guardarlo, una cartella copiata male e una cartella gia' aggiornata
    danno lo stesso resoconto: zero file copiati, nessun errore.
    """
    if codice < 0:
        return "robocopy non ha restituito un codice"
    if codice == 0:
        return "niente da fare, era già tutto allineato"
    pezzi = []
    if codice & 1:
        pezzi.append("file copiati")
    if codice & 2:
        pezzi.append("file in più sulla destinazione")
    if codice & 4:
        pezzi.append("file non corrispondenti")
    if codice & 8:
        pezzi.append("alcuni file non sono stati copiati")
    if codice & 16:
        pezzi.append("errore grave, nessun file copiato")
    return ", ".join(pezzi) if pezzi else f"codice non riconosciuto {codice}"


def testo_barra(
    nome: str, indice: int, totale: int, stimatore: Stimatore | None, trascorso: float, byte_fatti: int
) -> str:
    """Compone la barra di avanzamento, quaranta caratteri esatti.
    Ci stanno quattro informazioni e sono scelte in quest'ordine: a che punto
    e' la cartella in corso, quale cartella e' sul totale, a che punto e' il
    lavoro intero e quanto manca. Il nome della cartella viene per ultimo
    perche' e' l'unico che si puo' accorciare senza perdere una misura.
    Quaranta caratteri sono la larghezza di un display braille: uno sguardo
    delle dita, senza doverlo far scorrere.
    """
    if stimatore is None or stimatore.modo == "nessuna":
        # Senza storia e senza analisi la percentuale della cartella non
        # esiste: al suo posto va quel che si sa per certo, cioe' quanto e'
        # passato di la'. Meglio un dato vero che una percentuale inventata.
        tutto = stimatore.frazione() if stimatore else 0.0
        fisso = f"{indice}/{totale} tot{tutto * 100:3.0f}% {formatta_dimensione(byte_fatti)} "
    else:
        qui = stimatore.frazione_cartella(trascorso, byte_fatti)
        tutto = stimatore.frazione(trascorso, byte_fatti)
        mancano = stimatore.eta(trascorso, byte_fatti)
        tempo = formatta_durata(mancano) if mancano else "--:--"
        fisso = f"{qui * 100:3.0f}% {indice}/{totale} tot{tutto * 100:3.0f}% {tempo} "
    spazio = LARGHEZZA_BLOCCO - len(fisso)
    if spazio > 2:
        fisso += accorcia(nome, spazio)
    return fisso.ljust(LARGHEZZA_BLOCCO)[:LARGHEZZA_BLOCCO]


def mostra_barra(testo: str) -> None:
    """Scrive la barra a schermo, senza farla finire nel diario.
    Va sul flusso vero e non sullo sdoppiatore, altrimenti il diario di una
    sessione lunga si riempirebbe di migliaia di copie della stessa riga.
    Nel diario ci va invece una riga ogni tanto, che e' quel che serve dopo.
    """
    flusso = _stdout_originale or sys.stdout
    try:
        flusso.write("\r" + testo + "\r")
        flusso.flush()
    except (OSError, ValueError):
        pass


def pulisci_barra() -> None:
    """Cancella la barra prima di scrivere una riga normale."""
    mostra_barra(" " * LARGHEZZA_BLOCCO)


def _destinazione_raggiungibile(destinazione: str) -> bool:
    """Dice se la destinazione, o almeno un suo antenato, esiste davvero."""
    percorso = destinazione
    while percorso:
        if os.path.exists(percorso):
            return True
        genitore = os.path.dirname(percorso)
        if genitore == percorso:
            return False
        percorso = genitore
    return False


def _origini_esistenti(preset: dict) -> list[dict]:
    """Tiene le coppie la cui origine esiste, avvisando delle altre."""
    valide = []
    for coppia in preset["coppie_cartelle"]:
        if os.path.exists(percorso_lungo(coppia["origine"])):
            valide.append(coppia)
        else:
            print(f"Origine non trovata, saltata: {coppia['origine']}")
    return valide


def _storico_cartelle(preset: dict, macchina: str) -> dict:
    """Durate e byte di ogni cartella nell'ultima sessione su questa macchina."""
    voce = preset.get("storico_stats", {}).get(macchina, {})
    cartelle = voce.get("cartelle")
    return cartelle if isinstance(cartelle, dict) else {}


def _scadenza_da_rispettare(preset: dict) -> bool:
    """Chiede conferma se la periodicità non è ancora scaduta."""
    if not preset["ultimo_backup"]:
        return True
    try:
        ultimo = datetime.datetime.strptime(preset["ultimo_backup"], "%Y-%m-%d").date()
    except ValueError:
        return True
    if (datetime.date.today() - ultimo).days >= preset["giorni_periodicita"]:
        return True
    print("La periodicità non è ancora scaduta.")
    return conferma("Procedere comunque? ")


def _analizza_cartelle(cartelle: list[dict], destinazione: str, esclusioni: list[str]) -> dict:
    """Passata di sola lettura su tutte le coppie, per sapere quanto c'è da fare.
    Si esegue soltanto quando manca lo storico dei tempi, cioè la prima volta
    che un preset gira su una macchina. Dalla seconda in poi la stima viene
    dalla sessione precedente e questa scansione, che costa quanto la copia
    stessa quando la destinazione è in rete, non serve più.
    """
    print("Prima esecuzione di questo preset.")
    print("Analisi delle modifiche in corso.")
    previsti = {}
    for indice, coppia in enumerate(cartelle, start=1):
        nome = coppia["nome_cartella"]
        origine_piena = percorso_lungo(coppia["origine"])
        destinazione_piena = percorso_lungo(os.path.join(destinazione, nome))
        esito = conta_da_trasferire(
            origine_piena, destinazione_piena, esclusioni, nome=nome, indice=indice, totale=len(cartelle)
        )
        if esito is None:
            pulisci_barra()
            print(
                blocchi(
                    f"Analisi {indice} di {len(cartelle)}, {accorcia(nome, 18)}",
                    "non riuscita, stima incompleta",
                )
            )
            continue
        quanti, quanto = esito
        previsti[nome] = quanto
        pulisci_barra()
        print(
            blocchi(
                f"Analisi {indice} di {len(cartelle)}, {accorcia(nome, 18)}",
                f"{quanti} file, {formatta_dimensione(quanto)}",
            )
        )
        suona_avanzamento(indice / len(cartelle))
    return previsti


def _riga_cartella_conclusa(indice: int, totale: int, nome: str, dettaglio: dict) -> str:
    """La riga che si stampa quando una cartella è finita."""
    durata = formatta_durata(dettaglio["duration"]) if dettaglio["duration"] >= 1 else "meno di un secondo"
    if dettaglio["files_copied"]:
        fatto = f"{dettaglio['files_copied']} file, {formatta_dimensione(dettaglio['bytes_copied'])}"
    else:
        fatto = "niente da copiare"
    return blocchi(f"{indice} di {totale}, {accorcia(nome, 18)}", f"{fatto}, in {durata}")


def esegui_backup(preset_index: int | None = None, simulazione: bool = False) -> None:
    """Esegue, o simula, il backup di un preset."""
    impostazioni = carica_impostazioni()
    if impostazioni is None:
        return
    presets = impostazioni["presets"]
    macchina = id_macchina()

    if not presets:
        print("Nessun preset da eseguire.")
        pausa()
        return

    if preset_index is None:
        scelta = scegli_voce(_voci_dei_preset(presets), "Quale preset eseguo")
        if scelta is None:
            return
        preset = presets[scelta]
    else:
        if not 0 <= preset_index < len(presets):
            print("Preset non trovato.")
            return
        preset = presets[preset_index]

    doppioni = nomi_duplicati(preset)
    if doppioni:
        print("\nBackup non avviato: due o più origini")
        print("puntano alla stessa destinazione.")
        print(f"Nomi ripetuti: {', '.join(doppioni)}")
        print("Con /MIR la seconda copia cancella")
        print("i dati della prima. Rinominali dal")
        print("menu Modifica Preset prima di eseguire.")
        pausa()
        return

    stampa_dettaglio_esteso(preset)
    tipo_esecuzione = "simulazione" if simulazione else "backup reale"
    print(f"Stai per lanciare: {tipo_esecuzione}")
    if not conferma("Vuoi procedere? "):
        return

    if preset["machine_id"] != macchina and not simulazione:
        print(f"L'ID macchina non corrisponde: {preset['machine_id']}")
        if chiedi("Scrivi SI per forzare: ").strip().upper() != "SI":
            return

    destinazione = preset["root_destinazione"]
    if not _destinazione_raggiungibile(destinazione):
        print("La destinazione non è raggiungibile.")
        print(destinazione)
        print("Controlla la rete o che il disco sia montato.")
        pausa()
        return

    cartelle = _origini_esistenti(preset)
    if not cartelle:
        print("Nessuna cartella valida da copiare.")
        pausa()
        return

    if not simulazione and not _scadenza_da_rispettare(preset):
        return

    spegnere = False
    if not simulazione:
        # Non passa da conferma: lì invio vuol dire sì, e invio è il tasto
        # che si preme per riflesso. Spegnere il computer di qualcuno per un
        # riflesso non va bene, quindi qui si scrive SI per esteso.
        spegnere = chiedi("Spegnere il PC al termine? Scrivi SI: ").strip().upper() == "SI"

    inizio_sessione = time.time()
    cartella_log = os.path.join(destinazione, "Logs")
    try:
        os.makedirs(cartella_log, exist_ok=True)
        pulisci_log_vecchi(cartella_log, giorni_massimi=30)
    except OSError as e:
        print(f"Cartella dei log non disponibile: {e}")

    nomi = [c["nome_cartella"] for c in cartelle]
    storico_cartelle = _storico_cartelle(preset, macchina)
    ha_storia = any(
        isinstance(storico_cartelle.get(n), dict) and storico_cartelle[n].get("durata", 0) > 0 for n in nomi
    )
    previsti = {}
    if not ha_storia and not simulazione:
        previsti = _analizza_cartelle(cartelle, destinazione, preset.get("esclusioni", []))
    stimatore = Stimatore(nomi, storico_cartelle, previsti)
    print(stimatore.previsione_iniziale())

    lista_errori = []
    dettaglio_sessione = []
    totali = {
        "files_copied": 0,
        "files_failed": 0,
        "files_skipped": 0,
        "bytes_copied": 0,
        "bytes_skipped": 0,
        "files_deleted": 0,
        "snapshot_files": 0,
        "snapshot_bytes": 0,
        "snapshot_dirs": 0,
    }
    inizio_trasferimento = time.time()
    interrotto = False

    for indice, coppia in enumerate(cartelle, start=1):
        nome = coppia["nome_cartella"]
        origine_piena = percorso_lungo(coppia["origine"])
        destinazione_piena = percorso_lungo(os.path.join(destinazione, nome))
        marca = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
        file_log = os.path.join(cartella_log, f"{nome}-{marca}.txt")
        inizio_cartella = time.time()
        stimatore.inizia(nome)
        try:
            stats, byte_fatti, errori, cancellati, codice = esegui_robocopy(
                origine_piena,
                destinazione_piena,
                file_log,
                esclusioni=preset.get("esclusioni", []),
                simulazione=simulazione,
                stimatore=stimatore,
                nome_cartella=nome,
                indice=indice,
                totale=len(cartelle),
            )
        except KeyboardInterrupt:
            interrotto = True
            print("\nInterrotto. Le cartelle già concluse restano copiate.")
            break
        durata = time.time() - inizio_cartella
        stimatore.concludi(nome, durata, byte_fatti)
        lista_errori.extend(errori)
        totali["files_copied"] += stats["files_copied"]
        totali["files_failed"] += stats["files_failed"]
        totali["files_skipped"] += stats["files_skipped"]
        totali["bytes_copied"] += stats["bytes_copied"]
        totali["bytes_skipped"] += stats["bytes_skipped"]
        totali["files_deleted"] += cancellati
        totali["snapshot_files"] += stats["files_total"]
        totali["snapshot_bytes"] += stats["bytes_total"]
        totali["snapshot_dirs"] += stats["dirs_total"]
        dettaglio = {
            "nome": nome,
            "origine": coppia["origine"],
            "files_copied": stats["files_copied"],
            "bytes_copied": stats["bytes_copied"],
            "files_skipped": stats["files_skipped"],
            "files_failed": stats["files_failed"],
            "files_total": stats["files_total"],
            "bytes_total": stats["bytes_total"],
            "files_deleted": cancellati,
            "duration": durata,
            "previsti": previsti.get(nome),
        }
        dettaglio_sessione.append(dettaglio)
        annota(
            f"[cartella] {nome}: durata {durata:.1f}s, "
            f"byte previsti {previsti.get(nome, 'ignoti')}, "
            f"byte copiati {stats['bytes_copied']}, "
            f"file copiati {stats['files_copied']}, "
            f"file invariati {stats['files_skipped']}, "
            f"file cancellati {cancellati}, codice robocopy {codice}"
        )
        pulisci_barra()
        print(_riga_cartella_conclusa(indice, len(cartelle), nome, dettaglio))

    pulisci_barra()
    durata_totale = time.time() - inizio_sessione
    durata_trasferimento = time.time() - inizio_trasferimento
    suona_esito(totali["files_failed"] == 0 and not interrotto)

    precedente = preset.get("storico_stats", {}).get(macchina, {})
    if not simulazione and not interrotto:
        _aggiorna_storico(preset, macchina, dettaglio_sessione, totali, durata_totale, durata_trasferimento)
        salva_impostazioni(impostazioni)

    stampa_report(
        tipo_esecuzione,
        preset,
        dettaglio_sessione,
        totali,
        lista_errori,
        durata_totale,
        durata_trasferimento,
        precedente,
        interrotto,
    )

    if spegnere and not interrotto:
        spegni_il_computer()
    else:
        if os.path.exists(cartella_log):
            print(f"Log in {cartella_log}")
        pausa()


def spegni_il_computer(attesa: float = 60.0) -> bool:
    """Aspetta, e solo alla fine dell'attesa spegne davvero il computer.
    Il conto alla rovescia lo tiene Scriba, non Windows, e il comando parte
    per ultimo con tempo zero. La differenza conta: finché si aspetta non c'è
    niente di armato nel sistema, quindi chiudere Scriba, o vederselo chiudere
    da un guaio qualsiasi, annulla lo spegnimento invece di lasciarlo in
    agguato. Con il conto alla rovescia affidato a Windows, un programma che
    muore durante l'attesa lascia il computer che si spegne da solo, e chi lo
    sta usando si vede l'avviso senza sapere da dove arrivi.
    Qualunque tasto annulla, e anche CTRL+C. Restituisce True se lo
    spegnimento è stato davvero avviato.
    """
    print(f"Il computer si spegne fra {int(attesa)} secondi.")
    print("Premi un tasto qualsiasi per annullare.")
    scadenza = time.time() + attesa
    ultimo_avviso = 0.0
    try:
        while time.time() < scadenza:
            if tasto_premuto():
                print("Spegnimento annullato.")
                annota("[spegnimento] annullato da un tasto")
                return False
            mancano = scadenza - time.time()
            if mancano <= 10 and time.time() - ultimo_avviso >= 5:
                print(f"Mancano {int(mancano) + 1} secondi.")
                ultimo_avviso = time.time()
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("Spegnimento annullato.")
        annota("[spegnimento] annullato con CTRL+C")
        return False
    print("Spegnimento in corso.")
    annota("[spegnimento] avviato")
    chiudi_diario()
    subprocess.run(["shutdown", "/s", "/t", "0"], check=False)
    return True


def _aggiorna_storico(
    preset: dict,
    macchina: str,
    dettaglio: list[dict],
    totali: dict,
    durata_totale: float,
    durata_trasferimento: float,
) -> None:
    """Scrive nel preset com'è andata, cartella per cartella.
    Le durate delle singole cartelle sono quelle su cui si baserà la stima
    della prossima sessione, ed è il motivo per cui vengono conservate.
    Le cartelle non toccate stavolta mantengono la loro storia, perché
    toglierla vorrebbe dire ricominciare da zero al primo backup parziale.
    """
    oggi = datetime.date.today().strftime("%Y-%m-%d")
    storico = preset.setdefault("storico_stats", {})
    voce = storico.setdefault(macchina, {})
    cartelle = voce.get("cartelle")
    if not isinstance(cartelle, dict):
        cartelle = {}
    for det in dettaglio:
        cartelle[det["nome"]] = {
            "durata": round(det["duration"], 2),
            "byte": det["bytes_copied"],
            "file": det["files_copied"],
            "data": oggi,
        }
    velocita = totali["bytes_copied"] / durata_trasferimento if durata_trasferimento > 0 else 0
    voce.update(
        {
            "last_run_date": oggi,
            "total_files": totali["snapshot_files"],
            "total_bytes": totali["snapshot_bytes"],
            "total_dirs": totali["snapshot_dirs"],
            "files_copied": totali["files_copied"],
            "bytes_copied": totali["bytes_copied"],
            "files_skipped": totali["files_skipped"],
            "files_failed": totali["files_failed"],
            "duration_seconds": round(durata_totale, 2),
            "avg_speed": round(velocita, 2),
            "num_sources": len(dettaglio),
            "cartelle": cartelle,
        }
    )
    preset["ultimo_backup"] = oggi


def _con_segno(valore: float, formatta) -> str:
    """Formatta una variazione mettendoci davanti il segno."""
    segno = "+" if valore >= 0 else ""
    return f"{segno}{formatta(valore)}"


def stampa_report(
    tipo_esecuzione: str,
    preset: dict,
    dettaglio: list[dict],
    totali: dict,
    errori: list[dict],
    durata_totale: float,
    durata_trasferimento: float,
    precedente: dict,
    interrotto: bool,
) -> None:
    """Racconta com'è andata, in righe corte e senza tabelle a colonne.
    Le colonne allineate a spazi esistono soltanto per l'occhio: lette una
    dietro l'altra costringono a tenere a mente l'ordine delle intestazioni.
    Qui ogni riga porta con sé la propria etichetta.
    """
    print()
    print(f"Riepilogo della sessione, {tipo_esecuzione}")
    if interrotto:
        print("Sessione interrotta prima della fine.")
    print(f"Durata totale {formatta_durata(durata_totale)}")
    print(f"File copiati {totali['files_copied']}, {formatta_dimensione(totali['bytes_copied'])}")
    print(f"File già aggiornati {totali['files_skipped']}")
    print(f"File non riusciti {totali['files_failed']}")
    if totali["files_deleted"]:
        print(f"File cancellati dal mirror {totali['files_deleted']}")
    if durata_trasferimento > 0 and totali["bytes_copied"] > 0:
        velocita = totali["bytes_copied"] / durata_trasferimento
        print(f"Velocità media {formatta_dimensione(velocita)} al secondo")
    lavorati = totali["files_copied"] + totali["files_skipped"]
    if lavorati > 0:
        quota = totali["files_skipped"] / lavorati * 100
        print(f"File già aggiornati sul totale {quota:.1f} per cento")
    prossimo = datetime.date.today() + datetime.timedelta(days=preset["giorni_periodicita"])
    print(f"Prossimo backup {prossimo:%Y-%m-%d}, ogni {preset['giorni_periodicita']} giorni")

    if dettaglio:
        print()
        print("Dettaglio per cartella")
        for det in dettaglio:
            durata = formatta_durata(det["duration"]) if det["duration"] >= 1 else "meno di un secondo"
            if det["files_copied"]:
                velocita = det["bytes_copied"] / det["duration"] if det["duration"] > 0 else 0
                print(
                    f"{det['nome']}, {det['files_copied']} file, "
                    f"{formatta_dimensione(det['bytes_copied'])}, in {durata}, "
                    f"a {formatta_dimensione(velocita)} al secondo"
                )
            else:
                print(f"{det['nome']}, niente da copiare, in {durata}")
            if det.get("previsti") is not None:
                print(
                    f"   previsti {formatta_dimensione(det['previsti'])}, "
                    f"trasferiti {formatta_dimensione(det['bytes_copied'])}"
                )

    lente = sorted(dettaglio, key=lambda d: d["duration"], reverse=True)[:5]
    if len(dettaglio) > 5 and lente:
        print()
        print("Le cinque cartelle più lente")
        for posto, det in enumerate(lente, start=1):
            print(
                f"{posto}. {det['nome']}, {formatta_durata(det['duration'])}, "
                f"{formatta_dimensione(det['bytes_copied'])}"
            )

    print()
    ultima = precedente.get("last_run_date", "mai")
    print(f"Confronto con la sessione precedente, {ultima}")
    prima_byte = precedente.get("total_bytes", 0)
    prima_file = precedente.get("total_files", 0)
    prima_dirs = precedente.get("total_dirs", 0)
    if not prima_byte and not prima_file:
        print("Non c'è ancora niente con cui confrontare.")
        print(
            f"Adesso l'archivio è {formatta_dimensione(totali['snapshot_bytes'])}, "
            f"{totali['snapshot_files']} file, {totali['snapshot_dirs']} cartelle."
        )
    else:
        _confronta_archivio(totali, prima_byte, prima_file, prima_dirs)
        _confronta_operazioni(totali, precedente)
        _confronta_prestazioni(durata_totale, durata_trasferimento, totali, precedente)
        _tasso_di_crescita(totali, precedente, ultima)

    if totali["files_failed"] > 0:
        print()
        print(f"Attenzione, {totali['files_failed']} operazioni non riuscite.")
        if errori and len(errori) <= 10:
            for err in errori:
                print(f"Errore {err['code_hex']}, {err['detail']}")
        else:
            print("I dettagli sono nei log della destinazione.")


def _confronta_archivio(totali: dict, prima_byte: int, prima_file: int, prima_dirs: int) -> None:
    """Quanto è cresciuto l'archivio dall'ultima volta."""
    diff_byte = totali["snapshot_bytes"] - prima_byte
    diff_file = totali["snapshot_files"] - prima_file
    diff_dirs = totali["snapshot_dirs"] - prima_dirs
    perc_byte = (diff_byte / prima_byte * 100) if prima_byte else 0.0
    perc_file = (diff_file / prima_file * 100) if prima_file else 0.0
    perc_dirs = (diff_dirs / prima_dirs * 100) if prima_dirs else 0.0
    print(
        f"Dimensioni, prima {formatta_dimensione(prima_byte)}, "
        f"adesso {formatta_dimensione(totali['snapshot_bytes'])}, "
        f"{_con_segno(diff_byte, formatta_dimensione)}, {_con_segno(perc_byte, lambda v: f'{v:.2f}%')}"
    )
    print(
        f"File, prima {prima_file}, adesso {totali['snapshot_files']}, "
        f"{_con_segno(diff_file, str)}, {_con_segno(perc_file, lambda v: f'{v:.2f}%')}"
    )
    print(
        f"Cartelle, prima {prima_dirs}, adesso {totali['snapshot_dirs']}, "
        f"{_con_segno(diff_dirs, str)}, {_con_segno(perc_dirs, lambda v: f'{v:.2f}%')}"
    )


def _confronta_operazioni(totali: dict, precedente: dict) -> None:
    """Cosa ha fatto questa sessione rispetto alla precedente."""
    if not precedente.get("files_copied") and not precedente.get("files_skipped"):
        return
    print(f"File copiati, prima {precedente.get('files_copied', 0)}, adesso {totali['files_copied']}")
    print(
        f"Dati trasferiti, prima {formatta_dimensione(precedente.get('bytes_copied', 0))}, "
        f"adesso {formatta_dimensione(totali['bytes_copied'])}"
    )
    print(
        f"File già aggiornati, prima {precedente.get('files_skipped', 0)}, adesso {totali['files_skipped']}"
    )
    print(f"File non riusciti, prima {precedente.get('files_failed', 0)}, adesso {totali['files_failed']}")


def _confronta_prestazioni(
    durata_totale: float, durata_trasferimento: float, totali: dict, precedente: dict
) -> None:
    """Se questa sessione è andata più svelta o più piano della precedente."""
    prima_durata = precedente.get("duration_seconds", 0)
    if prima_durata <= 0:
        return
    differenza = durata_totale - prima_durata
    verso = "in più" if differenza >= 0 else "in meno"
    print(
        f"Durata, prima {formatta_durata(prima_durata)}, adesso {formatta_durata(durata_totale)}, "
        f"{formatta_durata(abs(differenza))} {verso}"
    )
    prima_velocita = precedente.get("avg_speed", 0)
    adesso_velocita = totali["bytes_copied"] / durata_trasferimento if durata_trasferimento > 0 else 0
    if prima_velocita > 0 and adesso_velocita > 0:
        scarto = (adesso_velocita - prima_velocita) / prima_velocita * 100
        print(
            f"Velocità, prima {formatta_dimensione(prima_velocita)} al secondo, "
            f"adesso {formatta_dimensione(adesso_velocita)} al secondo, "
            f"{_con_segno(scarto, lambda v: f'{v:.1f}%')}"
        )


def _tasso_di_crescita(totali: dict, precedente: dict, ultima: str) -> None:
    """Di quanto cresce l'archivio al giorno, e dove arriverebbe in un anno."""
    if ultima == "mai":
        return
    try:
        giorno = datetime.datetime.strptime(ultima, "%Y-%m-%d").date()
    except ValueError:
        return
    giorni = (datetime.date.today() - giorno).days
    diff_byte = totali["snapshot_bytes"] - precedente.get("total_bytes", 0)
    diff_file = totali["snapshot_files"] - precedente.get("total_files", 0)
    if giorni <= 0 or diff_byte == 0:
        return
    al_giorno = diff_byte / giorni
    file_al_giorno = diff_file / giorni
    print(f"Crescita, {giorni} giorni dall'ultimo backup")
    print(f"Dati {_con_segno(al_giorno, formatta_dimensione)} al giorno")
    print(f"File {_con_segno(file_al_giorno, lambda v: f'{v:.1f}')} al giorno")
    print(f"Proiezione su un anno {_con_segno(al_giorno * 365, formatta_dimensione)}")


# --- FUNZIONI DI MENU ---

MENU_PRINCIPALE = {
    "backup": "Esegui il backup",
    "simulazione": "Esegui una simulazione, senza copiare niente",
    "vedi": "Vedi i preset e le scadenze",
    "nuovo": "Crea un preset",
    "modifica": "Modifica un preset",
    "elimina": "Elimina un preset",
    "guida": "Manuale di Scriba",
    "controlla": "Controlla se c'e' una versione nuova",
    "dona": "Sostieni chi scrive questi programmi",
    ".": "Esci",
}

MENU_MODIFICA = {
    "generali": "Titolo, periodicita' e destinazione",
    "aggiungi": "Aggiungi una cartella di origine",
    "togli": "Togli una cartella di origine",
    "escludi": "Aggiungi un'esclusione",
    "riammetti": "Togli un'esclusione",
    "macchina": "Adotta il preset su questa macchina",
    ".": "Indietro",
}


def crea_nuovo_preset():
    """Guida la creazione di un preset nuovo."""
    print(f"{APP_NAME}, nuovo preset")
    titolo = chiedi("Titolo del backup: ").strip()
    if not titolo:
        print("Senza titolo non si va avanti.")
        return
    giorni = chiedi_numero("Ogni quanti giorni va rifatto? ", 1, 3650, 30)

    print("Scegli la cartella di destinazione.")
    destinazione = scegli_cartella(f"Destinazione per {titolo}")
    if not destinazione:
        print("Nessuna destinazione scelta, preset non creato.")
        return

    nuovo_preset = copy.deepcopy(MODELLO_PRESET)
    nuovo_preset["titolo"] = titolo
    nuovo_preset["machine_id"] = id_macchina()
    nuovo_preset["giorni_periodicita"] = giorni
    nuovo_preset["root_destinazione"] = destinazione

    while True:
        print(f"Origini inserite: {len(nuovo_preset['coppie_cartelle'])}")
        if not conferma("Aggiungere un'origine? "):
            break
        percorso = scegli_cartella("Cartella di origine")
        if percorso:
            usati = [c["nome_cartella"] for c in nuovo_preset["coppie_cartelle"]]
            nome = nome_destinazione(percorso, usati)
            nuovo_preset["coppie_cartelle"].append({"origine": percorso, "nome_cartella": nome})
            print(blocchi(f"Aggiunta {accorcia(percorso, 34)}", f"va in {nome}"))

    while True:
        print(f"Esclusioni inserite: {len(nuovo_preset['esclusioni'])}")
        if not conferma("Escludere una cartella? "):
            break
        esclusa = scegli_cartella("Cartella da escludere")
        if esclusa:
            nuovo_preset["esclusioni"].append(esclusa)
            print(f"Esclusa {os.path.basename(esclusa)}")

    impostazioni = carica_impostazioni()
    if impostazioni:
        impostazioni["presets"].append(nuovo_preset)
        if salva_impostazioni(impostazioni):
            print("Preset salvato.")


def modifica_preset() -> None:
    """Modifica un preset esistente."""
    impostazioni = carica_impostazioni()
    if not impostazioni or not impostazioni["presets"]:
        print("Nessun preset da modificare.")
        return
    scelta = scegli_voce(_voci_dei_preset(impostazioni["presets"]), "Quale preset modifico")
    if scelta is None:
        return
    preset = impostazioni["presets"][scelta]

    while True:
        print(f"\nModifica di {preset['titolo']}")
        voce = menu(MENU_MODIFICA, show=True, keyslist=True, p="Cosa vuoi fare? ")
        if voce is None or voce == ".":
            return

        if voce == "generali":
            nuovo_titolo = chiedi(f"Titolo [{preset['titolo']}]: ").strip()
            if nuovo_titolo:
                preset["titolo"] = nuovo_titolo
            giorni = chiedi_numero(
                f"Giorni [{preset['giorni_periodicita']}]: ", 1, 3650, preset["giorni_periodicita"]
            )
            preset["giorni_periodicita"] = giorni
            if conferma("Cambiare la destinazione? "):
                nuova = scegli_cartella("Nuova destinazione")
                if nuova:
                    preset["root_destinazione"] = nuova
            salva_impostazioni(impostazioni)

        elif voce == "aggiungi":
            percorso = scegli_cartella("Cartella di origine")
            if percorso:
                usati = [c["nome_cartella"] for c in preset["coppie_cartelle"]]
                nome = nome_destinazione(percorso, usati)
                preset["coppie_cartelle"].append({"origine": percorso, "nome_cartella": nome})
                salva_impostazioni(impostazioni)
                print(blocchi(f"Aggiunta {accorcia(percorso, 34)}", f"va in {nome}"))

        elif voce == "togli":
            if not preset["coppie_cartelle"]:
                print("Nessuna origine da togliere.")
                continue
            voci = [(c["nome_cartella"], c["origine"]) for c in preset["coppie_cartelle"]]
            dx = scegli_voce(voci, "Quale origine tolgo")
            if dx is None:
                continue
            bersaglio = preset["coppie_cartelle"][dx]
            nome_cartella_dest = bersaglio["nome_cartella"]
            radice = preset["root_destinazione"]
            print(f"Stai togliendo {bersaglio['origine']}")
            print("Vuoi eliminare anche la copia che sta")
            print("nella destinazione?")
            print(os.path.join(radice, nome_cartella_dest))
            if chiedi("Scrivi SI per cancellarla, invio per tenerla: ").strip().upper() == "SI":
                da_togliere = percorso_lungo(os.path.join(radice, nome_cartella_dest))
                if os.path.exists(da_togliere):
                    try:
                        shutil.rmtree(da_togliere)
                        print("Cartella eliminata dal disco.")
                    except OSError as e:
                        print(f"Errore nell'eliminazione: {e}")
                else:
                    print("Cartella non trovata sul disco.")
            else:
                print("I dati sul disco non sono stati toccati.")
            preset["coppie_cartelle"].pop(dx)
            salva_impostazioni(impostazioni)
            print("Voce tolta dal preset.")

        elif voce == "escludi":
            esclusa = scegli_cartella("Cartella da escludere")
            if esclusa:
                preset["esclusioni"].append(esclusa)
                salva_impostazioni(impostazioni)
                print(f"Esclusa {os.path.basename(esclusa)}")

        elif voce == "riammetti":
            if not preset["esclusioni"]:
                print("Nessuna esclusione presente.")
                continue
            voci = [(os.path.basename(e.rstrip("\\/")) or e, e) for e in preset["esclusioni"]]
            dx = scegli_voce(voci, "Quale esclusione riammetto")
            if dx is None:
                continue
            tolta = preset["esclusioni"].pop(dx)
            salva_impostazioni(impostazioni)
            print(f"Riammessa {tolta}")

        elif voce == "macchina":
            preset["machine_id"] = id_macchina()
            salva_impostazioni(impostazioni)
            print(f"Preset adottato da {preset['machine_id']}")


def elimina_preset():
    """Elimina un preset, dopo averne mostrato il contenuto e chiesto conferma.
    Prima bastava digitare il numero: un tasto sbagliato portava via origini,
    esclusioni e storico di quella macchina, senza una domanda.
    """
    impostazioni = carica_impostazioni()
    if not impostazioni or not impostazioni["presets"]:
        print("Nessun preset.")
        return
    scelta = scegli_voce(_voci_dei_preset(impostazioni["presets"]), "Quale preset elimino")
    if scelta is None:
        return
    preset = impostazioni["presets"][scelta]
    print(f"\nStai per eliminare: {preset['titolo']}")
    print(f"Destinazione: {preset['root_destinazione']}")
    print(f"Origini: {len(preset['coppie_cartelle'])}")
    print(f"Esclusioni: {len(preset['esclusioni'])}")
    print(f"Storico: {len(preset['storico_stats'])} macchine")
    print("I dati sul disco restano dove sono,")
    print("si perde soltanto la configurazione.")
    if chiedi("Scrivi SI per eliminare: ").strip().upper() != "SI":
        print("Niente eliminato.")
        return
    impostazioni["presets"].pop(scelta)
    if salva_impostazioni(impostazioni):
        print("Preset eliminato.")


def _stato_scadenza(preset: dict) -> str:
    """Da quanto e' scaduto un preset, o fra quanto scadra'."""
    if not preset["ultimo_backup"]:
        return "mai eseguito"
    try:
        ultimo = datetime.datetime.strptime(preset["ultimo_backup"], "%Y-%m-%d").date()
    except ValueError:
        return "data illeggibile"
    passati = (datetime.date.today() - ultimo).days
    mancano = preset["giorni_periodicita"] - passati
    if mancano < 0:
        return f"scaduto da {abs(mancano)} giorni"
    if mancano == 0:
        return "scade oggi"
    return f"fra {mancano} giorni"


def _voci_dei_preset(elenco: list[dict]) -> list[tuple[str, str]]:
    """Prepara i preset per il menu: si sceglie scrivendone il titolo.
    La descrizione porta quel che serve per non sbagliare bersaglio, cioe'
    quante origini ha il preset, di quale macchina e' e come sta con la
    scadenza. Prima si sceglieva contando i numeri in un elenco, ed era il
    modo piu' facile per lavorare sul preset sbagliato.
    """
    voci = []
    for preset in elenco:
        descrizione = (
            f"{len(preset['coppie_cartelle'])} origini, "
            f"{preset['machine_id'] or 'macchina sconosciuta'}, "
            f"{_stato_scadenza(preset)}"
        )
        voci.append((preset["titolo"], descrizione))
    return voci


def visualizza_presets():
    """Elenca i preset, una riga per informazione e non a colonne."""
    impostazioni = carica_impostazioni()
    if not impostazioni or not impostazioni["presets"]:
        print("Nessun preset.")
        return
    print(f"\nPreset presenti: {len(impostazioni['presets'])}")
    for idx, p in enumerate(impostazioni["presets"], start=1):
        print()
        print(f"{idx}. {p['titolo']}")
        print(f"   macchina {p['machine_id'] or 'sconosciuta'}")
        print(f"   ultimo backup {p['ultimo_backup'] or 'mai'}, {_stato_scadenza(p)}")
        print(f"   origini {len(p['coppie_cartelle'])}, destinazione {p['root_destinazione']}")
    pausa()


def mostra_scadenze():
    """All'avvio dice quali backup sono scaduti, e offre di eseguirli."""
    impostazioni = carica_impostazioni()
    if not impostazioni:
        return
    macchina = id_macchina()
    conteggio = {macchina: 0}
    scaduti_qui = []
    for idx, p in enumerate(impostazioni.get("presets", [])):
        suo = p.get("machine_id") or "sconosciuta"
        conteggio.setdefault(suo, 0)
        scaduto = False
        ultimo = p.get("ultimo_backup")
        if not ultimo:
            scaduto = True
        else:
            try:
                giorno = datetime.datetime.strptime(ultimo, "%Y-%m-%d").date()
                scaduto = (datetime.date.today() - giorno).days >= p["giorni_periodicita"]
            except ValueError:
                scaduto = True
        if scaduto:
            conteggio[suo] += 1
            if suo == macchina:
                scaduti_qui.append(idx)

    print("\nStato delle scadenze")
    print(f"{macchina}, questa macchina: {conteggio[macchina]} scaduti")
    for suo, quanti in conteggio.items():
        if suo != macchina:
            print(f"{suo}: {quanti} scaduti")

    if scaduti_qui and conferma("Eseguo ora i backup scaduti di questa macchina? "):
        for i in scaduti_qui:
            esegui_backup(i)


def mostra_guida():
    """Apre il manuale con il pager di GBUtils."""
    percorso = file_di_supporto(NOME_MANUALE)
    if not os.path.exists(percorso):
        print("Il manuale non e' insieme al programma.")
        print(f"Cercato in {percorso}")
        return
    manuale(percorso)


def controlla_aggiornamenti(solo_se_compilato: bool = True) -> bool:
    """Chiede a GitHub se c'e' una versione nuova e, se serve, la applica.
    All'avvio si guarda soltanto da eseguibile, perche' da sorgente
    l'aggiornamento non potrebbe comunque essere installato; quando invece e'
    l'utente a chiederlo dal menu si guarda sempre, cosi' almeno sa se e'
    uscita una versione nuova.
    Restituisce True quando il programma deve chiudersi perche'
    l'aggiornamento sta per essere applicato.
    """
    try:
        return gestisci_aggiornamento(APP_NAME, APP_VERSION, API_RELEASE, solo_se_compilato=solo_se_compilato)
    except Exception as e:  # noqa: BLE001
        print(f"Controllo aggiornamenti non riuscito: {e}")
        annota(f"[aggiornamenti] {e}")
        return False


def main():
    diario = apri_diario()
    scalda_audio()
    try:
        print(f"{APP_NAME} versione {APP_VERSION} del {RELEASE_DATE}")
        print("di Gabriele Battaglia (IZ4APU)")
        print(f"Macchina: {id_macchina()}")
        if diario:
            pulisci_log_vecchi(CARTELLA_DIARI, giorni_massimi=30)
        if controlla_aggiornamenti(solo_se_compilato=True):
            return
        mostra_scadenze()
        while True:
            voce = menu(MENU_PRINCIPALE, show=True, keyslist=True, p=f"\n{APP_NAME}, cosa faccio? ")
            if voce is None or voce == ".":
                break
            if voce == "backup":
                esegui_backup(simulazione=False)
            elif voce == "simulazione":
                esegui_backup(simulazione=True)
            elif voce == "vedi":
                visualizza_presets()
            elif voce == "nuovo":
                crea_nuovo_preset()
            elif voce == "modifica":
                modifica_preset()
            elif voce == "elimina":
                elimina_preset()
            elif voce == "guida":
                mostra_guida()
            elif voce == "dona":
                Donazione()
            elif voce == "controlla" and controlla_aggiornamenti(solo_se_compilato=False):
                break
        if diario:
            print("Diario di questa sessione:")
            print(diario)
    except KeyboardInterrupt:
        print("\nInterrotto.")
    finally:
        chiudi_diario()


if __name__ == "__main__":
    main()
