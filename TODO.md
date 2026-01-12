# TODO List - Ottimizzazione Scriba

## Priorità Alta (Performance)
- [ ] **Eliminare `get_dir_stats` (os.walk):** Attualmente lo script esegue scansioni Python lente prima e dopo ogni backup.
    - *Soluzione:* Dedurre le dimensioni "Prima" e "Dopo" direttamente dai log di Robocopy o tramite calcolo differenziale (Size Iniziale nota dal DB/Precedente + Copiati - Eliminati).
    - *Obiettivo:* Dimezzare i tempi di attesa pre/post copia.

- [x] **Gestione Output Robocopy:** Semplificata la gestione dell'output eliminando la barra di progresso in tempo reale a causa di limitazioni nel buffering di sistema.

## Robustezza
- [x] **Parsing Robocopy Agnostico:**
    - Rendere il parsing dei log indipendente dalla lingua di sistema (basandosi sulla struttura delle colonne).

## Interfaccia e Accessibilità
- [ ] **Sostituzione `wxPython` con Input CLI:**
    - Sostituire i dialoghi grafici di selezione cartella con un sistema puramente CLI per rimuovere la dipendenza pesante.
    - **Vincolo Critico:** `tkinter` non è accessibile. La soluzione deve rimanere fruibile via screen reader (es. input path manuale con validazione o navigazione testuale semplice).

## Varie
- [ ] **Refactoring Codice:** Pulizia generale, typing hints e docstrings.
- [ ] **Gestione Errori:** Migliorare il catch delle eccezioni durante la copia (file in uso, percorsi troppo lunghi non gestiti).
