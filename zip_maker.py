# Scriba, utilita': prepara l'archivio per la distribuzione.
# Autori: Gabriele Battaglia (IZ4APU) & ClaudIA (Claude Opus 5, modalita' auto).
# 04/09/2026: primo chiamante, il mestiere sta in crea_archivio_release di GBUtils.
# 06/09/2026: fuori anche la cartella dei diari di sessione.

"""Comprime la cartella prodotta da PyInstaller in un solo archivio.

Tutto il mestiere sta in GBUtils, cosi' la regola sulle esclusioni e' una
sola per tutti i progetti. Qui restano soltanto i nomi di Scriba.

Prima non c'era nessuno script e l'archivio si preparava a mano: gli
strumenti di compressione di Windows aggiungono una cartella di livello
superiore, che perform_update non sa attraversare.

La cartella compilata si chiama scriba minuscolo, l'archivio Scriba con
la maiuscola, come le release gia' pubblicate.

Si lasciano fuori le impostazioni. La loro copia di sicurezza finisce per
bak ed e' gia' saltata d'ufficio, come i log dei backup, che pero' Scriba
scrive nella cartella di destinazione e non accanto all'eseguibile.
"""

import sys

from GBUtils import crea_archivio_release

# La barra finale dice a crea_archivio_release che diari e' una cartella e non
# un file: ci nascono i diari di sessione al primo avvio dell'eseguibile, anche
# durante la prova che precede il rilascio, e non devono finire nel pacchetto.
FUORI = ["scriba_settings.json", "diari/"]


def main():
    try:
        crea_archivio_release("Scriba", cartella_dist="dist/scriba", escludi=FUORI)
    except (FileNotFoundError, OSError) as e:
        print(f"Archivio non creato: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
