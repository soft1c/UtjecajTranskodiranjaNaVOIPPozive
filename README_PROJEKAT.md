# Ispitno okruženje za VoIP transkodiranje

Praktični dio završnog rada **„Utjecaj transkodiranja na kvalitetu VoIP poziva“**.

## Namjena

Okruženje poredi RTP signal ulaznog kraka A sa signalom izlaznog kraka B. Time se mjeri dodatna promjena nastala u FreeSWITCH poslužitelju, a ne ukupni kvalitet u odnosu na izvorni nekodirani govor.

Pri rekonstrukciji oba kraka koriste se RTP redni brojevi i vremenske oznake. Intervali koji bi nestali prostim spajanjem korisnih sadržaja vraćaju se kao nulti PCM okviri prije vremenskog poravnanja i računanja metrika.

```text
Krajnji uređaj A  ->  FreeSWITCH (B2BUA)  ->  Krajnji uređaj B
    (pjsua)             obrada medija              (pjsua)
    kodek A                                         kodek B
```

FreeSWITCH radi u Docker kontejneru, dok oba pjsua klijenta rade na računaru domaćinu. Saobraćaj prolazi lokalnim interfejsom i snima se u PCAP datoteke.

## Brzo pokretanje

```bash
# Skripta će sama koristiti sudo samo za sistemske pakete.
bash scripts/setup/install_dependencies.sh

docker compose -f docker/docker-compose.yml up -d --build
docker exec voip-freeswitch fs_cli -x "sofia status"

source venv/bin/activate
python scripts/test/run_single_test.py \
  --codec-a PCMU --codec-b G722 --test-id T007 --iteration 1

# Konačna mjerenja uvijek izvršavati sekvencijalno (zadano ponašanje).
python scripts/test/run_full_matrix.py --iterations 10
```

Paralelni režim (`--parallel`) služi samo funkcionalnim probama. Istovremeni pozivi dijele kontejner, mrežni interfejs i CPU mjerenje, pa takvi rezultati nisu pogodni za završnu analizu.

## Testna matrica

Matrica sadrži svih 25 usmjerenih konfiguracija pet kodeka:

- pet konfiguracija bez transkodiranja;
- dvadeset konfiguracija s transkodiranjem;
- deset ponavljanja po konfiguraciji, odnosno 250 poziva.

## Rezultati

```text
results/raw/                          pojedinačni JSON, PCAP i CPU zapisi
results/summary/results_summary.csv  glavni tabelarni izvor rezultata
results/summary/results_summary.json glavni mašinski čitljiv sažetak
results/summary/charts/               generisani grafikoni
results/signal_analysis_initial_vs_final_20260901/
                                       spektralna analiza izvornog WAV-a i B-lega
```

Ponovno generisanje sažetka, grafikona i LaTeX tabele bez ponavljanja poziva:

```bash
source venv/bin/activate
python scripts/test/run_full_matrix.py --iterations 10 --skip-tests
python scripts/analysis/generate_charts.py
python scripts/analysis/generate_latex_results.py
```

Spektralna analiza svih postojećih poziva, bez njihovog ponovnog izvođenja:

```bash
python scripts/analysis/analyze_initial_vs_final_spectrum.py
```

## Metrike

| Metrika | Implementacija | Namjena |
|---|---|---|
| PESQ MOS-LQO | `pesq` | perceptivna razlika između kraka A i kraka B |
| STOI | `pystoi` | očuvanje razumljivosti |
| CPU | `docker stats` | relativno opterećenje kontejnera |
| relativni pomak | unakrsna korelacija | poravnanje signala, ne mrežno kašnjenje |

## Struktura

```text
docker/          Docker slika i konfiguracija FreeSWITCH-a
audio/reference referentni govorni signal na 8, 16 i 48 kHz
audio/recorded  dekodirani i pomoćni zvučni zapisi
scripts/setup   instalacija PJSIP-a i zavisnosti
scripts/test    izvođenje pojedinačnog testa i matrice
scripts/analysis ekstrakcija RTP-a, metrike, grafikoni i LaTeX tabela
results/        sirovi i agregirani rezultati
rad/            LaTeX izvor i završni PDF
```

Prethodni sažetak i PDF sa 23 konfiguracije sačuvani su u
`results/archive_pre_full_matrix_20260828/` i nisu dio konačne analize.

Rezultati prije uvođenja rekonstrukcije vremenske linije u primarnu
A-leg → B-leg analizu sačuvani su u
`results/archive_pre_primary_timeline_fix_20260831/`.

## Kompilacija rada

```bash
cd rad
pdflatex diplomski.tex
bibtex diplomski
pdflatex diplomski.tex
pdflatex diplomski.tex
```

Službeni fakultetski znak nije uključen u repozitorij. Prije predaje treba provjeriti zahtijeva li ga važeći fakultetski predložak.
