from varianten import (aehnlichkeit, bewerte_treffer, bilde_varianten, koelner_phonetik,
                       normalisiere, praegende_bestandteile)


def test_normalisierung():
    assert normalisiere("Klärox-Tech GmbH") == "KLAEROX TECH GMBH"
    assert praegende_bestandteile("Klärox-Tech GmbH") == ["KLAEROX"]
    assert praegende_bestandteile("The Shop") == ["SHOP"]  # Rückfall auf längstes Wort


def test_koelner_phonetik():
    assert koelner_phonetik("Müller-Lüdenscheidt") == "65752682"
    assert koelner_phonetik("Meier") == koelner_phonetik("Mayr") == koelner_phonetik("Maier")
    assert koelner_phonetik("Klarox") == koelner_phonetik("Clarocks")


def test_varianten_enthalten_pflichtvarianten():
    satz = bilde_varianten("Klarox")
    s = satz.suchstrings()
    assert "KLAROX" in s and "*KLAROX*" in s and "KLAR*" in s
    assert any(v.art == "schreibweise" and "CLAROX" in v.suchstring for v in satz.varianten)
    assert "KL*R*X" in s
    assert len(s) <= 40


def test_varianten_mehrwort():
    satz = bilde_varianten("Vita Nova Services")
    s = satz.suchstrings()
    assert "VITA NOVA SERVICES" in s and "VITANOVASERVICES" in s
    assert "*VITA*NOVA*" in s
    assert satz.staemme == ["VITA", "NOVA"]


def test_bewertung():
    assert bewerte_treffer("Klarox", "KLAROX")["score"] == 1.0
    assert bewerte_treffer("Klarox", "CLAROCKS")["stufe"] == "hoch"
    assert bewerte_treffer("Klarox", "KLAROX PLUS")["stufe"] == "hoch"
    assert bewerte_treffer("Klarox", "BANANENSAFT")["stufe"] == "niedrig"
    assert 0.3 < aehnlichkeit("Klarox", "Klarex") < 1.0


def test_phonetischer_wortanfang_kunstbegriff():
    s = bilde_varianten("CIRVO").suchstrings()
    assert "ZIR*" in s and "SIR*" in s and "KIR*" in s
    s = bilde_varianten("Sarumo").suchstrings()
    assert "ZARU*" in s and "CARU*" in s
    s = bilde_varianten("Vandelix").suchstrings()
    assert "FAND*" in s and "WAND*" in s
    assert not any(v.art == "phon_anfang" for v in bilde_varianten("Fit").varianten)
