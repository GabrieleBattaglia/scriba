# Prove automatiche di Scriba, parte impostazioni e utilità pure.
# Autori: Gabriele Battaglia (IZ4APU) & ClaudIA (Claude Opus 5, modalità auto).
# Coprono i punti in cui si perdono dati in silenzio: nomi di destinazione
# ripetuti, scelte numeriche fuori intervallo, preset incompleti, salvataggio
# interrotto a metà.

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scriba


class TestNomeDestinazione:
    def test_nome_semplice(self):
        assert scriba.nome_destinazione(r"C:\Utenti\Gabriele\Documenti") == "Documenti"

    def test_barre_in_avanti_come_le_restituisce_tkinter(self):
        assert scriba.nome_destinazione("C:/Utenti/Gabriele/Musica") == "Musica"

    def test_barra_finale_ignorata(self):
        assert scriba.nome_destinazione(r"C:\Utenti\Gabriele\Foto\\") == "Foto"

    def test_radice_di_disco(self):
        assert scriba.nome_destinazione("D:\\") == "D"

    def test_doppione_prende_il_nome_della_cartella_superiore(self):
        nome = scriba.nome_destinazione(r"E:\Lavoro\Documenti", ["Documenti"])
        assert nome == "Lavoro-Documenti"

    def test_doppione_del_doppione_prende_un_numero(self):
        usati = ["Documenti", "Lavoro-Documenti"]
        nome = scriba.nome_destinazione(r"E:\Lavoro\Documenti", usati)
        assert nome == "Documenti-2"

    def test_confronto_senza_distinzione_di_maiuscole(self):
        nome = scriba.nome_destinazione(r"E:\Lavoro\DOCUMENTI", ["documenti"])
        assert nome != "DOCUMENTI"


class TestNomiDuplicati:
    def test_nessun_duplicato(self):
        preset = {
            "coppie_cartelle": [
                {"origine": "a", "nome_cartella": "Uno"},
                {"origine": "b", "nome_cartella": "Due"},
            ]
        }
        assert scriba.nomi_duplicati(preset) == []

    def test_duplicato_trovato_anche_con_maiuscole_diverse(self):
        preset = {
            "coppie_cartelle": [
                {"origine": "a", "nome_cartella": "Documenti"},
                {"origine": "b", "nome_cartella": "documenti"},
            ]
        }
        assert scriba.nomi_duplicati(preset) == ["Documenti"]


class TestScegliVoce:
    """La scelta si fa scrivendo il nome, non contando i numeri di un elenco.
    Contarli era il modo piu' facile per lavorare sul preset sbagliato.
    """

    def _finto_menu(self, monkeypatch, risposta):
        visti = {}

        def finto(elenco, **_):
            visti.update(elenco)
            return risposta

        monkeypatch.setattr(scriba, "menu", finto)
        return visti

    def test_restituisce_la_posizione_della_voce_scelta(self, monkeypatch):
        self._finto_menu(monkeypatch, "Musica")
        voci = [("Documenti", "primo"), ("Musica", "secondo")]
        assert scriba.scegli_voce(voci) == 1

    def test_il_punto_annulla(self, monkeypatch):
        self._finto_menu(monkeypatch, ".")
        assert scriba.scegli_voce([("Uno", "x")]) is None

    def test_niente_scelto_annulla(self, monkeypatch):
        self._finto_menu(monkeypatch, None)
        assert scriba.scegli_voce([("Uno", "x")]) is None

    def test_elenco_vuoto_non_apre_nemmeno_il_menu(self, monkeypatch):
        def esplode(*a, **k):
            raise AssertionError("il menu non doveva aprirsi")

        monkeypatch.setattr(scriba, "menu", esplode)
        assert scriba.scegli_voce([]) is None

    def test_c_e_sempre_la_voce_per_annullare(self, monkeypatch):
        visti = self._finto_menu(monkeypatch, ".")
        scriba.scegli_voce([("Uno", "x")])
        assert "." in visti

    def test_le_descrizioni_arrivano_al_menu(self, monkeypatch):
        visti = self._finto_menu(monkeypatch, ".")
        scriba.scegli_voce([("Documenti", "venti origini")])
        assert visti["Documenti"] == "venti origini"

    def test_due_voci_con_lo_stesso_nome_restano_distinte(self, monkeypatch):
        visti = self._finto_menu(monkeypatch, "Foto 2")
        voci = [("Foto", "la prima"), ("Foto", "la seconda")]
        assert scriba.scegli_voce(voci) == 1
        assert len(visti) == 3

    def test_il_confronto_ignora_maiuscole_e_spazi(self):
        assert scriba._chiave_di_menu("  Foto   mie ", {}) == "Foto mie"
        assert scriba._chiave_di_menu("Foto", {"foto": ""}) == "Foto 2"

    def test_etichetta_vuota_riceve_un_nome(self):
        assert scriba._chiave_di_menu("   ", {}) == "voce"


class TestValidaPreset:
    def test_campi_mancanti_vengono_completati(self):
        preset, _ = scriba.valida_preset({"titolo": "Prova"}, 1)
        for chiave in scriba.MODELLO_PRESET:
            assert chiave in preset

    def test_chiavi_sconosciute_non_si_perdono(self):
        preset, _ = scriba.valida_preset({"titolo": "Prova", "novita": 42}, 1)
        assert preset["novita"] == 42

    def test_titolo_mancante_riceve_un_nome(self):
        preset, avvisi = scriba.valida_preset({}, 3)
        assert preset["titolo"] == "Preset senza titolo 3"
        assert avvisi

    def test_periodicita_illeggibile_torna_al_valore_di_riserva(self):
        preset, avvisi = scriba.valida_preset({"titolo": "P", "giorni_periodicita": "molti"}, 1)
        assert preset["giorni_periodicita"] == 365
        assert any("periodicità" in a for a in avvisi)

    def test_periodicita_zero_rifiutata(self):
        preset, _ = scriba.valida_preset({"titolo": "P", "giorni_periodicita": 0}, 1)
        assert preset["giorni_periodicita"] == 365

    def test_data_illeggibile_azzerata(self):
        preset, avvisi = scriba.valida_preset({"titolo": "P", "ultimo_backup": "ieri"}, 1)
        assert preset["ultimo_backup"] is None
        assert any("illeggibile" in a for a in avvisi)

    def test_data_valida_conservata(self):
        preset, _ = scriba.valida_preset({"titolo": "P", "ultimo_backup": "2026-01-31"}, 1)
        assert preset["ultimo_backup"] == "2026-01-31"

    def test_coppia_senza_origine_scartata(self):
        grezzo = {
            "titolo": "P",
            "coppie_cartelle": [
                {"nome_cartella": "Orfana"},
                {"origine": r"C:\Dati", "nome_cartella": "Dati"},
            ],
        }
        preset, avvisi = scriba.valida_preset(grezzo, 1)
        assert len(preset["coppie_cartelle"]) == 1
        assert any("senza origine" in a for a in avvisi)

    def test_coppia_senza_nome_ne_riceve_uno(self):
        grezzo = {"titolo": "P", "coppie_cartelle": [{"origine": r"C:\Dati"}]}
        preset, _ = scriba.valida_preset(grezzo, 1)
        assert preset["coppie_cartelle"][0]["nome_cartella"] == "Dati"

    def test_nomi_ripetuti_segnalati(self):
        grezzo = {
            "titolo": "P",
            "coppie_cartelle": [
                {"origine": r"C:\A\Documenti", "nome_cartella": "Documenti"},
                {"origine": r"D:\B\Documenti", "nome_cartella": "Documenti"},
            ],
        }
        _, avvisi = scriba.valida_preset(grezzo, 1)
        assert any("ripetuti" in a for a in avvisi)
        assert any("/MIR" in a for a in avvisi)

    def test_storico_illeggibile_azzerato(self):
        preset, _ = scriba.valida_preset({"titolo": "P", "storico_stats": "boh"}, 1)
        assert preset["storico_stats"] == {}


class TestValidaImpostazioni:
    def test_dato_non_dizionario(self):
        dati, avvisi = scriba.valida_impostazioni(["niente"])
        assert dati == {"schema": scriba.VERSIONE_SCHEMA, "presets": []}
        assert avvisi

    def test_elenco_preset_illeggibile(self):
        dati, avvisi = scriba.valida_impostazioni({"presets": "boh"})
        assert dati["presets"] == []
        assert avvisi

    def test_voce_non_dizionario_saltata(self):
        dati, avvisi = scriba.valida_impostazioni({"presets": [42, {"titolo": "Buono"}]})
        assert len(dati["presets"]) == 1
        assert any("saltata" in a for a in avvisi)

    def test_schema_aggiunto(self):
        dati, _ = scriba.valida_impostazioni({"presets": []})
        assert dati["schema"] == scriba.VERSIONE_SCHEMA

    def test_schema_dal_futuro_segnalato(self):
        _, avvisi = scriba.valida_impostazioni({"schema": 99, "presets": []})
        assert any("recente" in a for a in avvisi)


class TestSalvataggio:
    @pytest.fixture
    def impostazioni_isolate(self, tmp_path, monkeypatch):
        percorso = str(tmp_path / "scriba_settings.json")
        monkeypatch.setattr(scriba, "FILE_IMPOSTAZIONI", percorso)
        return percorso

    def test_giro_completo_salva_e_rilegge(self, impostazioni_isolate):
        dati = {"presets": [{"titolo": "Prova", "root_destinazione": r"D:\Backup"}]}
        assert scriba.salva_impostazioni(dati) is True
        riletto = scriba.carica_impostazioni()
        assert riletto["presets"][0]["titolo"] == "Prova"
        assert riletto["schema"] == scriba.VERSIONE_SCHEMA

    def test_file_assente_da_impostazioni_vuote(self, impostazioni_isolate):
        assert scriba.carica_impostazioni() == {"schema": scriba.VERSIONE_SCHEMA, "presets": []}

    def test_copia_di_sicurezza_creata_al_secondo_salvataggio(self, impostazioni_isolate):
        scriba.salva_impostazioni({"presets": []})
        assert not os.path.exists(impostazioni_isolate + ".bak")
        scriba.salva_impostazioni({"presets": [{"titolo": "Due"}]})
        assert os.path.exists(impostazioni_isolate + ".bak")

    def test_la_copia_conserva_la_versione_precedente(self, impostazioni_isolate):
        scriba.salva_impostazioni({"presets": [{"titolo": "Prima"}]})
        scriba.salva_impostazioni({"presets": [{"titolo": "Seconda"}]})
        with open(impostazioni_isolate + ".bak", encoding="utf-8") as f:
            vecchio = json.load(f)
        assert vecchio["presets"][0]["titolo"] == "Prima"

    def test_nessun_file_temporaneo_lasciato_in_giro(self, impostazioni_isolate):
        scriba.salva_impostazioni({"presets": []})
        assert not os.path.exists(impostazioni_isolate + ".tmp")

    def test_dato_non_serializzabile_non_tocca_l_originale(self, impostazioni_isolate):
        scriba.salva_impostazioni({"presets": [{"titolo": "Buono"}]})
        assert scriba.salva_impostazioni({"presets": [{"titolo": {1, 2, 3}}]}) is False
        with open(impostazioni_isolate, encoding="utf-8") as f:
            rimasto = json.load(f)
        assert rimasto["presets"][0]["titolo"] == "Buono"
        assert not os.path.exists(impostazioni_isolate + ".tmp")

    def test_file_rovinato_non_azzera_le_impostazioni(self, impostazioni_isolate, capsys):
        with open(impostazioni_isolate, "w", encoding="utf-8") as f:
            f.write("{ questo non e' json")
        assert scriba.carica_impostazioni() is None
        assert "Impossibile leggere" in capsys.readouterr().out

    def test_salvataggio_di_none_non_fa_niente(self, impostazioni_isolate):
        assert scriba.salva_impostazioni(None) is False
        assert not os.path.exists(impostazioni_isolate)


class TestUtilita:
    @pytest.mark.parametrize(
        "byte, atteso",
        [
            (0, "0.00 B"),
            (512, "512.00 B"),
            (1024, "1.00 KB"),
            (1536, "1.50 KB"),
            (1048576, "1.00 MB"),
            (-2048, "-2.00 KB"),
        ],
    )
    def test_format_size(self, byte, atteso):
        assert scriba.formatta_dimensione(byte) == atteso

    @pytest.mark.parametrize(
        "secondi, atteso",
        [
            (0, "--:--"),
            (-5, "--:--"),
            (59, "00:59"),
            (61, "01:01"),
            (3661, "01:01:01"),
        ],
    )
    def test_format_eta(self, secondi, atteso):
        assert scriba.formatta_durata(secondi) == atteso

    def test_smart_truncate_lascia_stare_i_testi_corti(self):
        assert scriba.accorcia("breve", 20) == "breve"

    def test_smart_truncate_tiene_testa_e_coda(self):
        risultato = scriba.accorcia("A" * 30 + "B" * 30, 20)
        assert len(risultato) <= 20
        assert risultato.startswith("A")
        assert risultato.endswith("B")
        assert "..." in risultato

    def test_parse_robocopy_error_inglese(self):
        riga = "2026/09/06 10:00:00 ERROR 32 (0x00000020) Copying File C:\\dati\\a.txt"
        info = scriba.analizza_errore_robocopy(riga)
        assert info["code_dec"] == 32
        assert info["code_hex"] == "0x00000020"
        assert "a.txt" in info["detail"]

    def test_parse_robocopy_error_italiano(self):
        riga = "ERRORE 5 (0x00000005) Accesso negato"
        info = scriba.analizza_errore_robocopy(riga)
        assert info["code_dec"] == 5

    def test_parse_robocopy_error_riga_normale(self):
        assert scriba.analizza_errore_robocopy("   Nuovo file   1234   C:\\dati\\b.txt") is None

    def test_fix_long_path_aggiunge_il_prefisso(self):
        risultato = scriba.percorso_lungo(r"C:\dati\lunga")
        assert risultato.startswith("\\\\?\\")

    def test_fix_long_path_su_percorso_di_rete(self):
        risultato = scriba.percorso_lungo(r"\\server\condivisione\dati")
        assert risultato.startswith("\\\\?\\UNC\\")

    def test_fix_long_path_non_raddoppia_il_prefisso(self):
        gia_fatto = scriba.percorso_lungo(r"C:\dati")
        assert scriba.percorso_lungo(gia_fatto) == gia_fatto


class TestVociDeiPreset:
    def test_la_descrizione_dice_origini_macchina_e_scadenza(self):
        preset = {
            "titolo": "Gigante",
            "machine_id": "GabryBat | Utente",
            "giorni_periodicita": 21,
            "ultimo_backup": None,
            "coppie_cartelle": [{"origine": "a", "nome_cartella": "A"}],
        }
        voci = scriba._voci_dei_preset([preset])
        assert voci[0][0] == "Gigante"
        assert "1 origini" in voci[0][1]
        assert "GabryBat" in voci[0][1]
        assert "mai eseguito" in voci[0][1]

    def test_senza_macchina_lo_dice(self):
        preset = {
            "titolo": "Senza",
            "machine_id": "",
            "giorni_periodicita": 30,
            "ultimo_backup": None,
            "coppie_cartelle": [],
        }
        assert "sconosciuta" in scriba._voci_dei_preset([preset])[0][1]
