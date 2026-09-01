# End-to-end analiza: originalni WAV -> B-leg

Generisano: 2026-08-31T18:39:09

Ovaj direktorij je odvojen od vazecih rezultata rada. Postojeci pozivi nisu
ponovo pokretani i datoteke u `results/raw` nisu mijenjane.

## Metodologija

- Referenca je WAV koji je zaista reproduciran za izvorni kodek A.
- Iz svakog PCAP-a izdvojen je izlazni B-leg RTP tok i ponovo dekodiran.
- RTP timestamp praznine vracene su kao nulti PCM intervali, jer bi prosto
  spajanje payloada vremenski sabijalo signal.
- `pesq_wb_16k` koristi jedinstveni sirokopojasni postupak na 16 kHz radi
  usporedbe s postojecim radom.
- `pesq_native_mode` koristi NB na 8 kHz za izvore od 8 kHz, a WB na 16 kHz
  za G.722 i Opus izvore. NB i WB vrijednosti ne treba direktno rangirati kao
  jednu homogenu skalu.
- STOI je racunat na 16 kHz.

## Kompletnost

- Uspjesno obradjenih poziva: 250 / 250
- Neuspjelih analiza: 0
- Konfiguracija sa svih deset rezultata: 25 / 25

## Grupni deskriptivni rezultati

- PESQ WB, kontrole bez transkodiranja: 3.170
- PESQ WB, konfiguracije s transkodiranjem: 2.608
- STOI, kontrole bez transkodiranja: 0.983
- STOI, konfiguracije s transkodiranjem: 0.932

## Vazna ogranicenja

Ova analiza ukljucuje pocetno kodiranje kodekom A i izlazno kodiranje kodekom
B, pa nije zamjena za postojecu A-leg -> B-leg mjeru koja izoluje server.
Nulti PCM u RTP prazninama predstavlja konzervativnu rekonstrukciju; stvarni
krajnji uredjaj moze koristiti prikrivanje gubitka paketa. Opus se rekonstruise
u Ogg wrapper i zadrzava ranije dokumentovano ogranicenje te rekonstrukcije.
Rezultati su ukljuceni u poglavlja o rezultatima i zakljucku diplomskog rada.
