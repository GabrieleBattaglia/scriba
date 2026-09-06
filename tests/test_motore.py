# Prove automatiche di Scriba, parte motore.
# Autori: Gabriele Battaglia (IZ4APU) & ClaudIA (Claude Opus 5, modalità auto).
# Coprono il riconoscimento delle righe di robocopy, la lettura del sommario,
# la stima dei tempi e la traduzione dell'avanzamento in suono. Sono le parti
# che decidono se le percentuali e le stime dicono il vero.

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scriba


class TestComandoRobocopy:
    def test_analisi_e_copia_usano_gli_stessi_criteri(self):
        """I due comandi devono differire solo per /L e la verbosità.
        Se cambiano anche i criteri di selezione, la passata di analisi conta
        file che la copia non toccherà, e la stima nasce già sbagliata.
        """
        piano = scriba.comando_robocopy(r"C:\src", r"D:\dst", piano=True)
        copia = scriba.comando_robocopy(r"C:\src", r"D:\dst", piano=False)
        for flag in scriba.FLAG_SELEZIONE:
            assert flag in piano, f"{flag} manca nella passata di analisi"
            assert flag in copia, f"{flag} manca nella copia"

    def test_fft_presente_in_entrambe(self):
        piano = scriba.comando_robocopy(r"C:\src", r"D:\dst", piano=True)
        copia = scriba.comando_robocopy(r"C:\src", r"D:\dst", piano=False)
        assert "/FFT" in piano
        assert "/FFT" in copia

    def test_solo_il_piano_elenca_senza_copiare(self):
        piano = scriba.comando_robocopy(r"C:\src", r"D:\dst", piano=True)
        copia = scriba.comando_robocopy(r"C:\src", r"D:\dst", piano=False)
        assert "/L" in piano
        assert "/L" not in copia

    def test_la_simulazione_elenca_senza_copiare(self):
        simulata = scriba.comando_robocopy(r"C:\src", r"D:\dst", simulazione=True)
        assert "/L" in simulata

    def test_prefisso_dei_percorsi_lunghi_tolto(self):
        cmd = scriba.comando_robocopy("\\\\?\\C:\\src", "\\\\?\\UNC\\server\\cond")
        assert cmd[1] == r"C:\src"
        assert cmd[2] == r"\\server\cond"

    def test_esclusioni_utente_passate_a_robocopy(self):
        cmd = scriba.comando_robocopy(r"C:\src", r"D:\dst", esclusioni=[r"C:\src\cache"])
        assert r"C:\src\cache" in cmd

    def test_recovery_escluso_solo_dalla_radice_del_disco(self):
        radice = scriba.comando_robocopy("C:\\", r"D:\dst")
        dentro = scriba.comando_robocopy(r"C:\dati", r"D:\dst")
        assert "Recovery" in radice
        assert "Recovery" not in dentro


class TestRigheRobocopy:
    """Righe vere prese da un log di robocopy su Windows italiano."""

    def test_file_nuovo(self):
        riga = "\t    Nuovo file\t\t       5\tnormale.txt"
        assert scriba.analizza_riga_robocopy(riga) == ("copia", 5, "normale.txt")

    def test_file_nuovo_in_inglese(self):
        riga = "\t    New File\t\t    1024\tfoto.jpg"
        assert scriba.analizza_riga_robocopy(riga) == ("copia", 1024, "foto.jpg")

    def test_file_supplementare_e_una_cancellazione(self):
        riga = "\t  *File supplementare\t\t       8\tda_cancellare.txt"
        assert scriba.analizza_riga_robocopy(riga) == ("extra", 8, "da_cancellare.txt")

    def test_extra_inglese(self):
        riga = "\t   *EXTRA File \t\t     512\tvecchio.txt"
        assert scriba.analizza_riga_robocopy(riga) == ("extra", 512, "vecchio.txt")

    def test_nome_con_new_dentro_non_diventa_una_copia(self):
        """Il vecchio riconoscimento cercava new e newer in tutta la riga.
        Una cancellazione di un file chiamato news bastava a farla contare
        fra i byte da trasferire.
        """
        riga = "\t  *File supplementare\t\t     700\tnews_di_ieri.txt"
        assert scriba.analizza_riga_robocopy(riga)[0] == "extra"

    def test_riga_di_cartella_non_e_un_file(self):
        riga = "\tNuova directory       1\tC:\\dati\\sub\\"
        assert scriba.analizza_riga_robocopy(riga) is None

    def test_riga_di_cartella_senza_tag_non_e_un_file(self):
        riga = "\t                   9\tC:\\dati\\Musica\\"
        assert scriba.analizza_riga_robocopy(riga) is None

    def test_riga_vuota(self):
        assert scriba.analizza_riga_robocopy("") is None

    def test_riga_di_percentuale(self):
        assert scriba.analizza_riga_robocopy("  45.2%  ") is None

    def test_riga_di_intestazione(self):
        assert scriba.analizza_riga_robocopy("   ROBOCOPY  ::  Copia di file") is None


class TestSommario:
    SOMMARIO_ITALIANO = [
        "              Totale   Copiato  IgnorateNon corrispondentiNon riuscitaSupplementari",
        "Directory:         4         1         3         0         0         0",
        "     File:        12         3         9         0         0         1",
        "     Byte:    600000    150000    450000         0         0      8192",
        "   Durata:   0:00:02   0:00:01                       0:00:00   0:00:00",
    ]
    SOMMARIO_INGLESE = [
        "               Total    Copied   Skipped  Mismatch    FAILED    Extras",
        "    Dirs :         4         1         3         0         0         0",
        "   Files :        12         3         9         0         0         1",
        "   Bytes :    600000    150000    450000         0         0      8192",
        "   Times :   0:00:02   0:00:01                       0:00:00   0:00:00",
    ]

    def test_italiano(self):
        s = scriba.analizza_sommario(self.SOMMARIO_ITALIANO)
        assert s["dirs_total"] == 4
        assert s["files_total"] == 12
        assert s["files_copied"] == 3
        assert s["files_skipped"] == 9
        assert s["files_failed"] == 0
        assert s["bytes_copied"] == 150000

    def test_inglese(self):
        s = scriba.analizza_sommario(self.SOMMARIO_INGLESE)
        assert s["files_total"] == 12
        assert s["bytes_total"] == 600000

    def test_la_riga_dei_tempi_non_viene_scambiata_per_totali(self):
        """0:00:02 diventa tre numeri se si tolgono i due punti: la riga dei
        tempi va riconosciuta ed esclusa, altrimenti falsa le statistiche.
        """
        s = scriba.analizza_sommario(self.SOMMARIO_ITALIANO)
        assert s["bytes_total"] == 600000

    def test_file_non_riusciti_letti_dalla_colonna_giusta(self):
        righe = [
            "Directory:         4         1         3         0         0         0",
            "     File:        12         3         7         0         2         1",
            "     Byte:    600000    150000    450000         0      1024      8192",
        ]
        assert scriba.analizza_sommario(righe)["files_failed"] == 2

    def test_sommario_mancante_da_zeri(self):
        s = scriba.analizza_sommario(["niente di utile"])
        assert s["files_total"] == 0
        assert s["bytes_copied"] == 0

    def test_riga_di_file_non_viene_scambiata_per_sommario(self):
        righe = ["\t    Nuovo file\t\t       5\tC:\\anno 2026 01 02 03 04\\x.txt"]
        assert scriba.analizza_sommario(righe)["files_total"] == 0


class TestStimatore:
    def _storico(self, **durate):
        return {nome: {"durata": d, "byte": 0, "file": 0} for nome, d in durate.items()}

    def test_senza_storia_e_senza_piano_conta_le_cartelle(self):
        """Un numero ci deve essere sempre, anche se grossolano: il suono
        dell'avanzamento dipende da quello, e senza si resta muti per tutta
        la sessione, che e' quel che succedeva nelle simulazioni.
        """
        s = scriba.Stimatore(["a", "b", "c", "d"])
        assert s.modo == "nessuna"
        assert s.frazione() == 0.0
        s.concludi("a", 10, 0)
        s.concludi("b", 10, 0)
        assert s.frazione() == pytest.approx(0.5)
        assert s.eta() is None
        assert "nessuna previsione" in s.previsione_iniziale()

    def test_la_cartella_in_corso_vale_mezza(self):
        s = scriba.Stimatore(["a", "b"])
        s.concludi("a", 10, 0)
        s.inizia("b")
        assert s.frazione() == pytest.approx(0.75)

    def test_frazione_della_cartella_col_tempo(self):
        s = scriba.Stimatore(["a"], self._storico(a=100))
        s.inizia("a")
        assert s.frazione_cartella(25) == pytest.approx(0.25)

    def test_frazione_della_cartella_coi_byte(self):
        s = scriba.Stimatore(["a"], None, {"a": 1000})
        s.inizia("a")
        assert s.frazione_cartella(0, 250) == pytest.approx(0.25)

    def test_frazione_della_cartella_senza_cartella_in_corso(self):
        s = scriba.Stimatore(["a"], self._storico(a=100))
        assert s.frazione_cartella(50) == 0.0

    def test_con_lo_storico_stima_sul_tempo(self):
        s = scriba.Stimatore(["a", "b"], self._storico(a=60, b=140))
        assert s.modo == "tempo"
        assert s.totale_atteso == 200
        assert s.eta() == pytest.approx(200)

    def test_a_meta_del_lavoro_manca_la_meta_del_tempo(self):
        s = scriba.Stimatore(["a", "b"], self._storico(a=100, b=100))
        s.concludi("a", 100, 0)
        assert s.frazione() == pytest.approx(0.5)
        assert s.eta() == pytest.approx(100)

    def test_se_va_piu_lenta_la_stima_si_allunga(self):
        """La prima cartella ha impiegato il doppio del previsto: anche la
        seconda va data per il doppio, altrimenti la stima resta ottimista
        fino all'ultimo minuto.
        """
        s = scriba.Stimatore(["a", "b"], self._storico(a=100, b=100))
        s.concludi("a", 200, 0)
        assert s.fattore == pytest.approx(2.0)
        assert s.eta() == pytest.approx(200)

    def test_la_correzione_non_scappa(self):
        s = scriba.Stimatore(["a", "b"], self._storico(a=1, b=100))
        s.concludi("a", 10000, 0)
        assert s.fattore == scriba.Stimatore.CORREZIONE_MASSIMA

    def test_cartella_senza_storia_prende_la_media_delle_altre(self):
        s = scriba.Stimatore(["a", "b", "c"], self._storico(a=100, b=200))
        assert s.attesa["c"] == pytest.approx(150)

    def test_la_frazione_avanza_dentro_la_cartella_in_corso(self):
        s = scriba.Stimatore(["a", "b"], self._storico(a=100, b=100))
        s.inizia("a")
        assert s.frazione(50) == pytest.approx(0.25)

    def test_la_frazione_non_supera_mai_uno(self):
        s = scriba.Stimatore(["a"], self._storico(a=10))
        s.inizia("a")
        assert s.frazione(99999) == pytest.approx(1.0)

    def test_senza_storia_ma_con_il_piano_stima_sui_byte(self):
        s = scriba.Stimatore(["a", "b"], None, {"a": 1000, "b": 3000})
        assert s.modo == "byte"
        assert s.frazione(0, 1000) == pytest.approx(0.25)

    def test_la_velocita_si_misura_su_una_finestra_recente(self):
        s = scriba.Stimatore(["a"], None, {"a": 1000})
        s._campioni = [(100.0, 0), (110.0, 500)]
        assert s._velocita() == pytest.approx(50.0)

    def test_senza_campioni_la_velocita_non_si_inventa(self):
        s = scriba.Stimatore(["a"], None, {"a": 1000})
        assert s.eta() is None


class TestSuono:
    def test_a_zero_suona_la_nota_piu_grave(self):
        assert scriba.nota_avanzamento(0.0) == "c2"

    def test_a_cento_suona_la_nota_piu_acuta(self):
        assert scriba.nota_avanzamento(1.0) == "b8"

    def test_a_meta_sta_in_mezzo(self):
        nota = scriba.nota_avanzamento(0.5)
        assert nota[-1] == "5"

    def test_la_nota_sale_sempre_con_l_avanzamento(self):
        ottave = [int(scriba.nota_avanzamento(p / 100)[-1]) for p in range(101)]
        assert ottave == sorted(ottave)
        assert ottave[0] == scriba.OTTAVA_MINIMA
        assert ottave[-1] == scriba.OTTAVA_MINIMA + scriba.OTTAVE - 1

    def test_valori_fuori_scala_non_rompono_niente(self):
        assert scriba.nota_avanzamento(-1) == "c2"
        assert scriba.nota_avanzamento(5) == "b8"

    def test_tutte_le_note_sono_nomi_validi(self):
        for p in range(101):
            nota = scriba.nota_avanzamento(p / 100)
            assert nota[:-1] in scriba.SCALA


class TestBlocchi:
    def test_un_pezzo_corto_non_viene_riempito_a_vuoto(self):
        assert scriba.blocchi("ciao") == "ciao"

    def test_due_pezzi_stanno_ognuno_nel_suo_blocco(self):
        riga = scriba.blocchi("primo", "secondo")
        assert riga.index("secondo") == scriba.LARGHEZZA_BLOCCO

    def test_un_pezzo_lungo_non_viene_tagliato(self):
        lungo = "x" * 60
        assert lungo in scriba.blocchi(lungo, "dopo")


class _ProcessoFinto:
    """Finge un robocopy che sta lavorando e poi finisce."""

    def __init__(self, giri_vivo):
        self.giri = giri_vivo

    def poll(self):
        if self.giri > 0:
            self.giri -= 1
            return None
        return 0


class TestLetturaLog:
    def test_il_battito_arriva_anche_se_il_log_non_cresce(self, tmp_path, monkeypatch):
        """Mentre robocopy scandisce un albero grosso non scrive niente.
        Senza battito il ciclo resterebbe fermo dentro la lettura, non
        risponderebbe a un tasto e non potrebbe dire che è ancora vivo.
        """
        monkeypatch.setattr(scriba, "SECONDI_FRA_BATTITI", 0.05)
        log = tmp_path / "log.txt"
        log.write_text("", encoding="utf-8")
        letti = list(scriba._leggi_log_dal_vivo(str(log), _ProcessoFinto(20)))
        assert None in letti
        assert letti.count(None) >= 2

    def test_le_righe_arrivano_intere(self, tmp_path):
        log = tmp_path / "log.txt"
        log.write_text("prima\nseconda\r\nterza", encoding="utf-8")
        letti = [r for r in scriba._leggi_log_dal_vivo(str(log), _ProcessoFinto(0)) if r]
        assert letti == ["prima", "seconda", "terza"]

    def test_le_percentuali_separate_dal_ritorno_di_carrello(self, tmp_path):
        log = tmp_path / "log.txt"
        log.write_text("  1.0%\r  2.0%\r  3.0%\r", encoding="utf-8")
        letti = [r.strip() for r in scriba._leggi_log_dal_vivo(str(log), _ProcessoFinto(0)) if r]
        assert letti == ["1.0%", "2.0%", "3.0%"]

    def test_log_mai_creato_non_blocca(self, tmp_path):
        mancante = str(tmp_path / "non_esiste.txt")
        assert list(scriba._leggi_log_dal_vivo(mancante, _ProcessoFinto(0), attesa=0.1)) == []


class TestSpegnimento:
    """Lo spegnimento del computer e' l'azione piu' invadente che Scriba
    possa fare. Queste prove esistono perche' durante lo sviluppo un banco
    di prova rispose di si alla domanda e spense davvero la macchina di
    Gabriele, due volte.
    """

    def test_un_tasto_annulla_e_non_chiama_niente(self, monkeypatch):
        chiamate = []
        monkeypatch.setattr(scriba.subprocess, "run", lambda *a, **k: chiamate.append(a))
        monkeypatch.setattr(scriba, "tasto_premuto", lambda: True)
        assert scriba.spegni_il_computer(attesa=5) is False
        assert chiamate == []

    def test_ctrl_c_annulla_e_non_chiama_niente(self, monkeypatch):
        chiamate = []
        monkeypatch.setattr(scriba.subprocess, "run", lambda *a, **k: chiamate.append(a))

        def interrompe():
            raise KeyboardInterrupt

        monkeypatch.setattr(scriba, "tasto_premuto", interrompe)
        assert scriba.spegni_il_computer(attesa=5) is False
        assert chiamate == []

    def test_il_comando_parte_solo_alla_scadenza_e_con_tempo_zero(self, monkeypatch):
        """Il conto alla rovescia lo tiene Scriba, non Windows: finche' si
        aspetta non c'e' niente di armato nel sistema, quindi chiudere il
        programma annulla lo spegnimento invece di lasciarlo in agguato.
        """
        chiamate = []
        monkeypatch.setattr(scriba.subprocess, "run", lambda *a, **k: chiamate.append(a[0]))
        monkeypatch.setattr(scriba, "tasto_premuto", lambda: False)
        monkeypatch.setattr(scriba, "chiudi_diario", lambda: None)
        assert scriba.spegni_il_computer(attesa=0.1) is True
        assert chiamate == [["shutdown", "/s", "/t", "0"]]


class TestCodiceRobocopy:
    """Robocopy dice come e' andata con un codice di uscita fatto di bit.
    Senza guardarlo, una cartella copiata male e una gia' allineata danno
    lo stesso resoconto: zero file copiati e nessun errore.
    """

    def test_zero_vuol_dire_niente_da_fare(self):
        assert "niente da fare" in scriba.descrivi_codice_robocopy(0)

    def test_uno_vuol_dire_copiati(self):
        assert "copiati" in scriba.descrivi_codice_robocopy(1)

    def test_otto_e_un_guaio(self):
        assert "non sono stati copiati" in scriba.descrivi_codice_robocopy(8)

    def test_sedici_e_un_guaio_grave(self):
        assert "grave" in scriba.descrivi_codice_robocopy(16)

    def test_i_bit_si_sommano(self):
        descrizione = scriba.descrivi_codice_robocopy(3)
        assert "copiati" in descrizione
        assert "in più" in descrizione

    def test_codice_mancante(self):
        assert "non ha restituito" in scriba.descrivi_codice_robocopy(-1)


class TestBarra:
    """La barra e' larga quanto un display braille e sta fra due ritorni a
    capo: quello finale riporta il cursore a colonna zero, ed e' cio' che
    tiene il focus fermo sul principio della riga mentre il dato si aggiorna
    sotto le dita.
    """

    def _stimatore(self):
        s = scriba.Stimatore(["a", "b"], {"a": {"durata": 100}, "b": {"durata": 100}})
        s.inizia("a")
        return s

    def test_larga_esattamente_quaranta(self):
        riga = scriba.testo_barra("Documenti", 1, 20, self._stimatore(), 50, 0)
        assert len(riga) == scriba.LARGHEZZA_BLOCCO

    def test_larga_quaranta_anche_con_un_nome_lunghissimo(self):
        riga = scriba.testo_barra("N" * 200, 1, 20, self._stimatore(), 50, 0)
        assert len(riga) == scriba.LARGHEZZA_BLOCCO

    def test_larga_quaranta_anche_senza_stimatore(self):
        riga = scriba.testo_barra("Documenti", 1, 20, None, 50, 1024)
        assert len(riga) == scriba.LARGHEZZA_BLOCCO

    def test_porta_le_due_percentuali_e_il_conto_delle_cartelle(self):
        riga = scriba.testo_barra("Documenti", 3, 20, self._stimatore(), 50, 0)
        assert "50%" in riga
        assert "3/20" in riga
        assert "tot" in riga

    def test_il_nome_compare_se_ci_sta(self):
        assert "Doc" in scriba.testo_barra("Doc", 1, 2, self._stimatore(), 50, 0)

    def test_sta_fra_due_ritorni_a_capo(self, monkeypatch):
        scritto = []

        class FlussoFinto:
            def write(self, testo):
                scritto.append(testo)

            def flush(self):
                pass

        monkeypatch.setattr(scriba, "_stdout_originale", FlussoFinto())
        scriba.mostra_barra("x" * scriba.LARGHEZZA_BLOCCO)
        assert scritto[0].startswith("\r")
        assert scritto[0].endswith("\r")
        assert len(scritto[0]) == scriba.LARGHEZZA_BLOCCO + 2

    def test_senza_informazioni_mostra_i_byte_invece_di_una_percentuale_finta(self):
        s = scriba.Stimatore(["a", "b"])
        s.inizia("a")
        riga = scriba.testo_barra("Documenti", 1, 2, s, 10, 2048)
        assert len(riga) == scriba.LARGHEZZA_BLOCCO
        assert "2.00 KB" in riga
        assert "1/2" in riga


class TestSommarioDalLog:
    """Le statistiche si rileggono dal log a copia conclusa.
    Sul campo la lettura dal vivo si e' rotta a meta' di una cartella da
    dodici minuti, e con lei erano andate perse tutte le sue statistiche.
    A lavoro finito il file e' li', completo e fermo: e' la fonte migliore.
    """

    LOG = (
        "Avvio 2026-09-06 22:00:00\n"
        "Origine C:\\dati\n"
        "-------------------------------------------------------------\n"
        "\t    Nuovo file\t\t     100\tuno.txt\n"
        "-------------------------------------------------------------\n"
        "              Totale   Copiato  Ignorate\n"
        "Directory:         4         1         3         0         0         0\n"
        "     File:        12         3         9         0         0         1\n"
        "     Byte:    600000    150000    450000         0         0      8192\n"
        "   Durata:   0:00:02   0:00:01                       0:00:00   0:00:00\n"
    )

    def test_legge_i_totali_dal_file(self, tmp_path):
        log = tmp_path / "log.txt"
        log.write_text(self.LOG, encoding="utf-8")
        letto = scriba._sommario_dal_log(str(log))
        assert letto["files_copied"] == 3
        assert letto["bytes_copied"] == 150000

    def test_log_interrotto_a_meta_non_inventa_numeri(self, tmp_path):
        """E' il caso vero: robocopy troncato, nessun sommario nel file."""
        log = tmp_path / "log.txt"
        log.write_text(self.LOG.split("              Totale")[0], encoding="utf-8")
        assert scriba._sommario_dal_log(str(log)) is None

    def test_file_mancante_non_solleva(self, tmp_path):
        assert scriba._sommario_dal_log(str(tmp_path / "non_esiste.txt")) is None

    def test_il_log_finito_viene_spostato_a_destinazione(self, tmp_path):
        partenza = tmp_path / "temporaneo.txt"
        partenza.write_text("contenuto", encoding="utf-8")
        arrivo = tmp_path / "Logs" / "definitivo.txt"
        scriba._porta_a_destinazione(str(partenza), str(arrivo))
        assert arrivo.exists()
        assert not partenza.exists()

    def test_se_lo_spostamento_fallisce_il_log_resta_dov_e(self, tmp_path, monkeypatch):
        partenza = tmp_path / "temporaneo.txt"
        partenza.write_text("contenuto", encoding="utf-8")

        def non_si_puo(*a, **k):
            raise OSError("destinazione irraggiungibile")

        monkeypatch.setattr(scriba.shutil, "move", non_si_puo)
        scriba._porta_a_destinazione(str(partenza), str(tmp_path / "Logs" / "x.txt"))
        assert partenza.exists()
