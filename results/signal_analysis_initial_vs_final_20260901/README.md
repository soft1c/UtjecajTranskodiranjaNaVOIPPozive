# Eksplorativna analiza izvornog i krajnjeg signala

Analiza je izvedena nad svih 250 postojećih PCAP zapisa. Novi pozivi nisu
pokretani, a glavni rezultati rada nisu mijenjani.

## Postupak

- Izvor je WAV koji je stvarno reproduciran za kodek A.
- Krajnji signal je B-leg dekodiran iz PCAP-a i rekonstruisan prema RTP
  vremenskim oznakama.
- Signali su poravnati i svedeni na 16 kHz, kao u postojećoj end-to-end analizi.
- Spektralni udjeli računati su Welchovom procjenom snage u opsezima 80–300 Hz,
  300–3400 Hz, 3400–7000 Hz i 7000–7900 Hz.
- Za ilustracije je iz svake odabrane konfiguracije uzeta iteracija čiji je
  PESQ najbliži prosjeku te konfiguracije, da se izbjegne biranje ekstrema.

## Glavni nalazi

Najveći prosječni pad udjela energije u opsegu 3,4–7 kHz zabilježen je
za Opus → GSM
(-2,76 postotnih poena).

Za Opus → GSM udio iznosi 2,84%
u izvornom i 0,08% u krajnjem
signalu. Frekvencija ispod koje se nalazi 95% spektralne energije pritom se
pomjera sa 2763 Hz na
1646 Hz.

Kod GSM → Opus udio u istom opsegu ostaje vrlo nizak:
1,28% u izvornom i
0,09% u krajnjem signalu.
To je u skladu s činjenicom da kodiranje u širokopojasni format ne može vratiti
frekvencijski sadržaj izgubljen u prethodnom uskopojasnom koraku.

Prosječna promjena za širokopojasni izvor i uskopojasno odredište iznosi
-2,57 postotnih poena, a kada su oba kodeka širokopojasna
-0,59 postotnih poena.

Ovi pokazatelji su deskriptivni. Ne zamjenjuju PESQ ili STOI i ne treba ih
tumačiti kao zasebnu mjeru subjektivnog kvaliteta. Zaključci su ograničeni na
korišteni sintetizirani govorni uzorak i eksperimentalno okruženje.
