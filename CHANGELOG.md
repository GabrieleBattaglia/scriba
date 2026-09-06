# Cronologia di Scriba

Autori: Gabriele Battaglia (IZ4APU) & ClaudIA.
La voce della 3.0.0 è scritta insieme alle modifiche. Quelle precedenti sono ricostruite dalle release pubblicate su GitHub e dai messaggi di commit, quindi riportano soltanto le novità principali.

## 3.0.0, 2026-09-06

Refactoring profondo, fase 1. Il programma e' lo stesso, ma quasi tutto quel che c'e' sotto e' stato rifatto: il modo di stimare i tempi, il modo di leggere robocopy, il modo di parlare a chi lo usa e il modo di proteggere i dati. Le versioni dalla 2.8.4 alla 2.9.3 sono state tappe di lavoro nella stessa giornata e non sono mai state pubblicate.

### Sicurezza dei dati e delle configurazioni

Due origini con lo stesso nome finale finivano nella stessa cartella di destinazione, e con /MIR la seconda copia cancellava i dati della prima senza dirlo. Ora il nome di destinazione viene reso unico al momento dell'inserimento, anteponendo la cartella superiore o aggiungendo un numero, e un backup con nomi ripetuti non parte affatto.

Il file delle impostazioni veniva cercato nella directory di lavoro corrente: lanciando Scriba da un collegamento, da una riga di comando aperta altrove o da un'attivita' pianificata, i preset risultavano spariti. Ora sta sempre accanto al programma, sia da sorgente sia da eseguibile.

Nelle scelte numeriche un numero negativo veniva accettato come indice e prendeva l'ultimo elemento della lista: si poteva modificare o eliminare il preset sbagliato. Le scelte numeriche non esistono piu' del tutto, vedi piu' sotto.

L'eliminazione di un preset avveniva alla pressione del numero, senza conferma. Ora mostra titolo, destinazione, origini, esclusioni e macchine nello storico, e chiede di scrivere SI.

Le impostazioni vengono validate e completate al caricamento, con un numero di schema su cui appoggiare le migrazioni future. Un campo mancante non ferma piu' il programma con un errore incomprensibile, e nessun campo sconosciuto viene scartato.

Il salvataggio e' atomico: file temporaneo, rilettura di verifica, poi sostituzione. Prima un salvataggio andato male piu' uno successivo bastavano a portarsi via anche l'unica copia buona.

Lo spegnimento del computer a fine backup si chiede scrivendo SI per esteso e non con un invio, e il conto alla rovescia lo tiene Scriba invece di Windows, lanciando il comando solo alla scadenza. Finche' si aspetta non c'e' niente di armato nel sistema, quindi chiudere Scriba annulla lo spegnimento invece di lasciarlo in agguato. Qualunque tasto lo annulla.

### Avanzamento e stime

La passata di analisi girava senza /FFT mentre la copia lo usava, quindi le due passate non giudicavano gli stessi file. Verso una condivisione di rete, dove gli orari differiscono di un secondo, l'analisi prometteva molti piu' byte di quelli davvero trasferiti e la percentuale strisciava per tutta la sessione. Ora i flag che decidono cosa toccare stanno in un elenco unico, uguale per entrambe.

Le righe di robocopy venivano riconosciute cercando sottostringhe in tutta la riga, fra cui new e newer minuscoli: bastava un file chiamato news perche' una cancellazione venisse contata fra i byte da trasferire. Ora il riconoscimento si basa sulla struttura della riga, quindi funziona con Windows in qualunque lingua e distingue le copie dalle cancellazioni, che vengono anche contate e riportate.

Le statistiche finali si riconoscono dalla forma e non piu' dalle parole del sommario, cosi' la riga dei tempi non viene scambiata per una riga di totali.

La stima del tempo residuo si fonda sul tempo e non piu' sui byte. In un backup periodico quasi tutto il tempo se ne va a confrontare cartelle in cui non e' cambiato niente, che di byte ne valgono zero: la vecchia percentuale restava ferma per minuti e poi saltava. Ora Scriba conserva quanto e' durata ogni cartella e stima da li', correggendo in corsa con il rapporto fra il tempo speso e quello atteso. Alla prima esecuzione ripiega sui byte previsti, misurando la velocita' sugli ultimi trenta secondi invece che sulla media dall'inizio.

La passata di analisi si esegue solo alla prima esecuzione di un preset su una macchina. Dalla seconda in poi la stima viene dallo storico e quella scansione, che su una destinazione di rete costa quanto la copia, non si paga piu'.

Scriba legge il codice di uscita di robocopy e lo riferisce. Prima "non c'era niente da fare" e "robocopy e' fallito" davano lo stesso resoconto.

### Come Scriba parla

La barra di avanzamento e' larga esattamente quaranta caratteri, quanto un display braille, e si riscrive sul posto ogni due secondi fra due ritorni a capo. Quello finale riporta il cursore a colonna zero, cosi' il focus resta fermo sul principio della riga e le dita leggono un dato che si aggiorna sotto di loro. Porta la percentuale della cartella in corso, quale cartella e' sul totale, la percentuale complessiva, il tempo mancante e il nome della cartella nello spazio che avanza. Anche la passata di analisi ha la sua barra. La barra vive solo a schermo: nel diario va una riga di stato ogni due minuti.

L'avanzamento si sente. Una campanella breve, che nasce e si spegne da sola come una corda pizzicata, sale di tono da do2 a si8 insieme al backup e si sposta da sinistra a destra fra gli altoparlanti. Sette ottave. A fine sessione un accordo che sale se e' andata bene e scende se no.

Ogni scelta si fa scrivendo una parola, mai piu' contando numeri in un elenco: il preset da eseguire, da modificare, da eliminare, la cartella da togliere, l'esclusione da riammettere. Nella scelta del preset la descrizione dice quante origini ha, di quale macchina e' e come sta con la scadenza. Le conferme passano da invio o escape.

Il resoconto finale non ha piu' separatori grafici ne' tabelle a colonne allineate a spazi: ogni riga porta con se' la propria etichetta.

Nuove voci di menu: guida mostra il manuale, controlla cerca su GitHub se e' uscita una versione nuova, dona sostiene chi scrive questi programmi.

### Sotto il cofano

Il log di robocopy si scrive in locale e si sposta a destinazione a copia conclusa. Leggere dal vivo un file che un altro processo sta scrivendo su una condivisione di rete e' fragile: sul campo, su una cartella da dodici minuti, una lettura e' fallita e Scriba ha abbandonato robocopy mentre stava ancora specchiando, perdendo le statistiche di quella cartella e lasciando in giro un processo che nessuno aspettava. Ora il processo si aspetta sempre, anche quando la lettura si rompe, e le statistiche finali si rileggono dal log a lavoro finito, quando il file e' completo e fermo.

Diario di sessione: tutto quello che Scriba scrive a schermo, comprese le risposte digitate, finisce anche in un file dentro la cartella diari, accanto al programma. Serve per rileggere una sessione o consegnarla a chi deve capire un problema. Si ripulisce da solo dopo trenta giorni, come i log.

La codifica del log di robocopy si chiede al sistema invece di darla per cp850, e il log si legge a blocchi invece che un carattere alla volta.

Interrompendo con CTRL+C durante un backup Scriba non esce piu' con una traccia di errore: dice quali cartelle erano concluse e torna al menu.

Suite di prove automatiche con pytest, 130 prove. Ruff non riporta nulla, con ruff.toml del progetto che spiega una per una le deroghe. I nomi delle funzioni sono tutti in italiano.

scriba.spec torna nel repository e porta il manuale dentro l'eseguibile. Il manuale e' stato riscritto. Dal repository sono usciti l'archivio compilato da undici megabyte e la copia di sicurezza delle impostazioni.

## 2.8.3, 2026-07-09

Correzioni al progresso del backup e alla fase di analisi.

## 2.8.1, 2026-06-25

Accessibilità: il cursore viene riportato a colonna zero dopo la stampa della riga di avanzamento, così il display braille mostra la parte utile della riga e non gli spazi di riempimento.

## 2.8.0, 2026-06-19

Riprogettazione della riga di avanzamento e report statistico dettagliato, con confronto rispetto alla sessione precedente.

## 2.5.1, 2026-06-06

Ripristino della barra di avanzamento in tempo reale, in forma pulita e temporizzata.

## 2.4.3, 2026-02-03

Tolta la dipendenza da wxPython, sostituita da tkinter: pacchetto più leggero e più facile da portare.

## 1.0.1, 2025-11-23

Prima release pubblica.
