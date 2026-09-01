# Konačni paket završnog rada

Ovaj direktorij je izdvojena kopija materijala za predaju završnog rada
**„Utjecaj transkodiranja na kvalitetu VoIP poziva“** autora Ahmeda Softića.
Radni direktorij iz kojeg je paket napravljen nije izmijenjen.

Repozitorij sadrži i kompletan skup od 250 mjerenja, uključujući pripadajuće
PCAP i CPU zapise. Lozinke zapisane u FreeSWITCH konfiguraciji predstavljaju
isključivo testne vrijednosti za lokalno eksperimentalno okruženje.

## Glavne datoteke

- `Diplomski_rad.pdf` — završna kompajlirana verzija rada;
- `rad/` — LaTeX izvori, literatura, generisane tabele i slike korištene u PDF-u;
- `scripts/` — skripte za postavljanje okruženja, izvođenje testova i analizu;
- `results/raw/` — 250 pojedinačnih rezultata sa pripadajućim PCAP, WAV i zapisnim datotekama;
- `results/summary/` — zbirni CSV/JSON rezultati i grafikoni;
- `results/end_to_end_original_vs_bleg_20260828/` — dopunska end-to-end analiza;
- `results/signal_analysis_initial_vs_final_20260901/` — spektralno poređenje izvornog WAV-a i krajnjeg B-lega;
- `audio/reference/` — referentni govorni signal;
- `docker/` — korištena Docker i FreeSWITCH konfiguracija;
- `README_PROJEKAT.md` — detaljan opis eksperimentalnog okruženja;
- `requirements.txt` — Python zavisnosti.

Stare arhive rezultata, virtualno Python okruženje, privremene LaTeX datoteke i pomoćni snimci iz radnog direktorija nisu uključeni.

## Konačni skup rezultata

Analiza obuhvata 25 usmjerenih kombinacija pet kodeka i deset ponavljanja po konfiguraciji, odnosno ukupno 250 poziva. Prosječni PESQ MOS-LQO iznosi 4,242 bez transkodiranja i 3,401 s transkodiranjem. Prosječna dodatna degradacija iznosi 0,841 boda.

## Kompilacija rada

Iz korijena ovog direktorija:

```bash
cd rad
pdflatex -interaction=nonstopmode -halt-on-error diplomski.tex
bibtex diplomski
pdflatex -interaction=nonstopmode -halt-on-error diplomski.tex
pdflatex -interaction=nonstopmode -halt-on-error diplomski.tex
```

Grafikoni i LaTeX tabele mogu se ponovo generisati iz postojećih rezultata prema uputama u `README_PROJEKAT.md`, bez ponovnog izvođenja poziva.
