# Scriba, utilita': prepara l'archivio per la distribuzione.
# Autori: Gabriele Battaglia (IZ4APU) & ClaudIA (Claude Opus 5, modalita' auto).
# 04/09/2026: primo chiamante, il mestiere sta in crea_archivio_release di GBUtils V103.

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

FUORI = ["scriba_settings.json"]


def main():
    try:
        crea_archivio_release("Scriba", cartella_dist="dist/scriba", escludi=FUORI)
    except (FileNotFoundError, OSError) as e:
        print(f"Archivio non creato: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
