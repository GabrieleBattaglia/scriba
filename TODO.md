# TODO List - Ottimizzazione Scriba

## Priorità Alta (Performance)
- [x] **Eliminare `get_dir_stats` (os.walk):** Attualmente lo script esegue scansioni Python lente prima e dopo ogni backup.
    - *Soluzione:* Dedurre le dimensioni "Prima" e "Dopo" direttamente dai log di Robocopy o tramite calcolo differenziale (Size Iniziale nota dal DB/Precedente + Copiati - Eliminati).
    - *Obiettivo:* Dimezzare i tempi di attesa pre/post copia.

- [x] **Gestione Output Robocopy:** Semplificata la gestione dell'output eliminando la barra di progresso in tempo reale a causa di limitazioni nel buffering di sistema (ripristinata in modo pulito e temporizzato nella v2.5.1).

## Robustezza
- [x] **Parsing Robocopy Agnostico:**
    - Rendere il parsing dei log indipendente dalla lingua di sistema (basandosi sulla struttura delle colonne).

## Interfaccia e Accessibilità
- [x] **Sostituzione `wxPython` con Input CLI:**
    - Sostituire i dialoghi grafici di selezione cartella con un sistema puramente CLI per rimuovere la dipendenza pesante.
    - **Vincolo Critico:** `tkinter` per la scelta dei percorsi è integrata nativamente su Windows e accessibile tramite screen reader (NVDA).

## Varie
- [x] **Refactoring Codice:** Pulizia generale, rimozione bare except, standardizzazione e docstrings.
- [x] **Gestione Errori:** Miglioramento della cattura degli errori a runtime con segnalazione inline sul report finale (per errori <= 10).
