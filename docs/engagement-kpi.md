# Engagement readiness: catalogo dei KPI e degli indici

Documento di studio, versione v1, 2026-10-06; riconciliato il 2026-10-07 con i produttori dei KPI (`checks.py`, `deception.py`, `friction.py`, rubriche) e con `anchors.json`; riletti il 2026-10-08 i giudizi, il journey e la riproducibilità (sezioni 3.4, 4.2.5, 4.3-4.7, 5.1-5.3, 5.7-5.8, 6, 7.2, 8) per Jev come pilota del journey, giudice delle rubriche operative e fallback del crawler (`rubrics.v2`); ancore v2 (flag Provv. su `FAI.CHECKOUT_FIELDS` e `CCL.READABILITY`) il 2026-10-10. Che cosa misurare in uno shop e-commerce, dalla velocità di pagina e dalla disponibilità degli elementi fino al lato psicologico, e come convertirlo in KPI e indici misurabili e riproducibili.

| File collegato | Ruolo |
|---|---|
| `jev_ultrafast/engagement/kpis.py` | Registro degli 89 KPI: id, sotto-indice proprietario, unità, produttore, aggregazione, stadi del funnel |
| `jev_ultrafast/engagement/anchors.json` | Ancore di normalizzazione, pesi, soglie di pubblicazione (versione `anchors.v2`). È la fonte canonica dei numeri |
| `jev_ultrafast/engagement/schemas.py` | Forma dei dati: pagine, osservazioni, giudizi, punteggi |
| `jev_ultrafast/engagement/rubrics/*.json` | Rubriche a etichette chiuse dei 7 KPI giudicati, ciascuna con il giudice che la valuta (`judge`: `jev` o `claude`; versione `rubrics.v2`) |
| `jev_ultrafast/engagement/checks.py`, `deception.py`, `friction.py` | Produttori dei KPI deterministici, dei test di inganno e dei KPI di journey: le definizioni e i metodi della sezione 4 sono quelli che questi moduli implementano |
| `docs/engagement-design.md` | Architettura, contratti, protocollo dei giudizi, igiene di misura, matematica dello scoring (in inglese) |

I valori di soglia, ancora e peso riportati in questo documento sono le **ancore v2**. I numeri canonici vivono in `anchors.json`: se un valore del documento e quello del file differiscono, vale il file, e il documento va riallineato. Ogni KPI di `kpis.py` compare qui una sola volta (sezione 4): gli id elencati sono esattamente quelli del registro. `tests/test_engagement_docs.py` verifica id, punti e mappe delle ancore, stato pubblicato o provvisorio, unità, stadi e severità; le definizioni e i metodi sono riconciliati a mano con i produttori e vanno riletti quando un produttore cambia.

## In sintesi

- L'**ERS** (Engagement Readiness Score) è la media geometrica pesata di sei sotto-indici, PERF 18, FAI 22, TRI 20, PTI 15, CCL 15, MPI 10, con floor 10 per sotto-indice, moltiplicata per una penalità di rischio dark pattern (DPR) che toglie al massimo il 30%.
- I **89 KPI** si dividono per produttore: 64 deterministici da DOM, rete e screenshot (`checks`), 6 test di inganno eseguiti in contesti isolati freschi dello stesso profilo (`deception`: visite ripetute per timer, scorte e overlay insistenti, confronto a visita singola per sneak into basket, opzioni pre-selezionate e asimmetria del consenso), 12 dal journey dell'agente (`friction`) e 7 giudizi LLM su snippet con etichette chiuse (`judgments`): 5 rubriche operative le giudica Jev, il modello di scelta di TypeSafe (una richiesta per pagina), 2 rubriche di percezione e i casi che Jev non decide le giudica Claude.
- Un KPI non valutabile non vale mai 0. L'ERS si pubblica solo con copertura almeno del 60%. Il rapporto deve mostrare una **confidenza** A, B o C, che è un grado di copertura e non di qualità del punteggio, la quota di giudizio LLM e l'ambito dell'audit (deterministico, con o senza journey, con o senza giudizi LLM). L'etichetta "Confidenza" e la riga "Ambito" sono presenti nel rapporto HTML e JSON e nei riepiloghi di `score_run` e `get_report` (sezione 3.5).
- L'evidenza è più solida sulla frizione del checkout, sulla trasparenza dei costi, sulla fiducia (Baymard) e sulle soglie dei Core Web Vitals; è più debole su salienza della CTA, complessità visiva e persuasione.
- È una **stima da sessioni sintetiche**, non engagement misurato. Per un punteggio strutturale da esperti il tetto realistico di validità (PURE) è una correlazione di circa 0,5-0,6 con questionari di usabilità percepita e nessuna con tempi e completamento dei compiti.

## Come leggere il documento

**Forza dell'evidenza.** Usa la legenda dei file di ricerca.

| Grado di evidenza | Significato |
|---|---|
| Forte | Replicata o su larga scala, oppure norma o standard direttamente misurabile |
| Moderata | Studio con revisione tra pari o grande studio di settore, ma correlazionale o trasferibile solo in parte agli shop |
| Debole | Teoria, euristica di pratica o evidenza contestata |

Nelle tabelle dei KPI la colonna "Evidenza" gradua **il costrutto e la sua misura**; la colonna "Ancore" (Pubbl. o Provv., sotto) gradua **le soglie**. Sono due assi indipendenti: un costrutto ben fondato può avere soglie editoriali, e viceversa. Per le metriche di velocità, per esempio, il grado "Forte" riguarda la soglia pubblicata e non il legame con la conversione, che è osservazionale (sezione 4.1). Un grado senza suffisso è quello dei file di ricerca, compresi i gradi composti come "debole o moderata". Provenienza dei gradi:

1. Un grado dei file di ricerca si applica quando la ricerca ha graduato il costrutto che il KPI rende operativo, anche se la riga della ricerca è più ampia; il costrutto è nominato tra parentesi (per esempio `MPI.URGENCY_SIGNALS`: "Moderata (riga scarsità della ricerca)").
2. Altrimenti il grado è **(cat.)**, assegnato dal catalogo applicando la legenda alla fonte citata; la base, quando non è ovvia, segue il punto e virgola (per esempio "Moderata (cat.; proxy)").
3. Mai mescolare: un grado composto della ricerca ("debole o moderata") si riporta alla lettera, e un grado della ricerca non si media mai con uno del catalogo.

**Stato di verifica delle fonti.** I tag sono quelli dei file di ricerca. Un'affermazione senza tag è riportata dal file di ricerca senza che esso dichiari come l'ha verificata.

| Tag | Significato |
|---|---|
| [V] | Fonte primaria letta dal ricercatore |
| [S] | Visto solo in sintesi di motori di ricerca o pagine secondarie |
| [M] | Da conoscenza propria, non riverificato: da controllare prima di farci affidamento |
| vendor | Studio pubblicato dall'azienda interessata o commissionato da Google: tipicamente osservazionale e promosso dall'azienda |

**Stato delle ancore.** "Pubbl." e "Provv." riportano il flag `provisional` di `anchors.json` (false e true). "Pubbl." significa che i punti di normalizzazione **e** la grandezza misurata poggiano su una soglia, una norma citabile per articolo, una linea guida o un benchmark pubblicati, verificati ([V]) o verificabili con un riferimento esatto. Per i booleani non c'è una soglia da calibrare: la mappa 100/0 è l'unica possibile e la fonte dice perché il segnale conta, non quanto vale. Il "quanto vale" è il peso del KPI, che è una scelta editoriale per ogni KPI, pubblicato o provvisorio (sezione 3.3, punto 6): per un booleano il flag dice quindi soltanto se la sua rilevanza poggia su una linea guida pubblicata. Gli altri punti editoriali (per esempio lo 0 a due volte la soglia "scarso") sono indicati nella cella. "Provv." significa ancora editoriale, da calibrare: vi rientra anche una soglia pubblicata trasferita a una grandezza diversa da quella per cui fu definita (per esempio le bande INP applicate ai click dell'agente, o i campi Baymard dell'intero checkout applicati al solo primo step). Il flag serve a chi legge il rapporto (un asterisco accanto al KPI) e non cambia il punteggio.

---

## 1. Scopo e limiti

### 1.1 Che cosa significa "engagement readiness"

Per **engagement readiness** si intende la predisposizione di uno shop a essere usato fino in fondo da un visitatore: quanto è veloce, quanto sono presenti e raggiungibili gli elementi che servono per scegliere e comprare, quanta frizione si incontra lungo il percorso, quanto il sito riduce il rischio percepito, quanto è trasparente sui prezzi, quali motivatori genuini offre e quanti segnali di inganno contiene.

È una **stima da sessioni sintetiche**: un audit deterministico di cinque tipi di pagina (home, listing di categoria, pagina prodotto, carrello, primo step del checkout) e, su richiesta, un percorso eseguito da un agente con un obiettivo in linguaggio naturale. Non è engagement misurato: nessun utente reale viene osservato e nessun dato di conversione viene letto. Il punteggio complessivo si chiama **Engagement Readiness Score (ERS)**, mai "engagement score" né "conversion score".

I risultati sono di due nature: **predicted friction**, cioè frizione prevista dal confronto con soglie pubblicate e con ciò che un agente riesce a fare, e **risk signals**, cioè segnali di rischio osservati. Sono ipotesi ordinate per priorità, non diagnosi.

Ogni KPI del rapporto porta uno di cinque tag di tipo di informazione.

| Tag | Che cosa è | Esempio |
|---|---|---|
| Osservato | Fatto deterministico letto da DOM, rete o journey | Valore di LCP letto dal browser; numero di campi contati al primo step del checkout |
| Inferito | Giudizio di un LLM su uno snippet estratto, con etichetta chiusa e citazione verbatim | Politica di reso `vague` |
| Non valutabile | Il KPI non si è potuto valutare, con il motivo | `bot_challenge`, `checkout_boundary`, `background_tab` |
| Non applicabile | Il KPI non esiste per questa esecuzione e non entra nella copertura | KPI di journey in un audit senza journey; prodotto senza varianti |
| Informativo | Valore riportato ma con peso 0 nel sotto-indice | `FAI.ACTIONS_TO_GOAL` |

**Mappatura sul modello HEART di Google** (ricerca, sezione C3). Il task success è misurabile direttamente (`FAI.JOURNEY_SUCCESS`, con oracolo indipendente). L'engagement è misurabile solo come azioni, pagine e profondità per sessione. Happiness, Adoption e Retention non sono misurabili in modo valido da un agente: il SUS o il SEQ compilati da un LLM soffrono di sycophancy e non sono usati come metrica.

### 1.2 Che cosa un agente non può misurare

Elenco ricavato dalla ricerca (psicologia, sezione 5.1; performance, sezione C4) e dai rischi di progetto.

- **Emozione e intento reali**, e la motivazione di B=MAP: dalla pagina si osserva solo l'offerta di motivatori.
- **Differenze individuali e demografiche.** Le preferenze estetiche variano con età, istruzione e cultura (Reinecke e Gajos 2014 [S]).
- **Contesto fuori dalla pagina:** familiarità con il marchio, competitività del prezzo, mix di traffico, reputazione offline, esperienza post-acquisto.
- **Varianti e stato:**
  - test A/B e personalizzazione: l'agente vede una sola variante;
  - geo, stato dei cookie, differenze di bot detection, stato del banner di consenso;
  - flussi con autenticazione, come la disdetta e l'area account;
  - timer e scorte dinamici per sessione, che richiedono visite ripetute.
- **Realismo dell'agente.** Il comportamento di un agente LLM non coincide con quello umano: gli autori di UXAgent lo giudicano utile per iterare il disegno di uno studio ma "non del tutto realistico" [S].
- **Confusione tra fallimento dell'agente e frizione del sito.** Un journey fallito può dipendere dai limiti dell'agente (sezione 7).
- **Limiti di rilevazione.** I conteggi di Mathur et al. sono limiti inferiori; il loro crawler raggiunse il checkout solo su 66 pagine campione su 100 e si fermò prima del pagamento [V]. Lo stesso punto cieco vale qui, dove l'audit si ferma al primo step del checkout per scelta.
- **Contenuti non leggibili.** Gli shadow DOM aperti sono letti da `audit.js`, ma i controlli che il crawler clicca e che i journey offrono vengono da `snapshot.js`, che non entra negli shadow root: le pagine di listing e di prodotto (e il carrello, dopo il click di aggiunta al carrello, quando nello shadow root c'è solo il suo link) si caricano comunque dal loro URL, ma un passo che deve cliccare un controllo dentro uno shadow root, come l'aggiunta al carrello, chiude lì il funnel con un motivo (per esempio `add_to_cart_failed` o `checkout_cta_not_found`) e le fasi successive sono non valutabili. Shadow DOM chiusi e iframe cross-origin (widget di recensioni, moduli di pagamento) sono coperti solo in parte. L'audit registra i conteggi nelle pagine raccolte (`a11y.iframes`, `a11y.shadow_roots_closed`) e `report.py` li usa già in un caso: ai KPI giudicati letti come assenza senza LLM (sezione 6.1, punto 7) aggiunge l'avvertenza "possibile widget non letto (iframe o shadow DOM chiuso sulla pagina)". Il rapporto non contiene ancora un elenco generale di questi limiti: elenca gli stadi non valutabili con il loro motivo e quell'avvertenza; l'elenco completo sta nel README e nella sezione 11 di `docs/engagement-design.md`; vedi anche la sezione 5.6 di questo documento.
- **Dati di laboratorio, non di campo.** Cache fredda, una sola località, un solo dispositivo emulato, nessuna interazione reale. I dati di campo sono quelli con cui "dare priorità" (web.dev).

### 1.3 Linguaggio

| Si usa | Non si usa |
|---|---|
| engagement readiness, Engagement Readiness Score (ERS) | engagement misurato, engagement score, conversion score |
| predicted friction (nel rapporto: attrito previsto) | frizione "misurata" o "dimostrata" |
| risk signals, dark-pattern risk signals (nel rapporto: segnali di rischio; non è un accertamento giuridico) | violazioni, infrazioni |
| "associato a" con fonte e forza dell'evidenza | "causa", "fa aumentare le conversioni", "meno scelta vende di più" |
| non valutabile, con il motivo | punteggio 0 per una pagina non raggiungibile |

Nomi dei sotto-indici nel rapporto (`report.py`): **Prestazioni** (PERF), **Attrito previsto** (FAI, punteggio alto = attrito previsto basso), **Segnali di fiducia** (TRI), **Trasparenza di prezzi e costi** (PTI), **Chiarezza e carico cognitivo** (CCL), **Leve di persuasione genuine** (MPI), **Segnali di rischio dark pattern** (DPR). I nomi inglesi della ricerca (sezione 5.2: Performance, Predicted friction, Trust signals observable, Price-transparency signals, Clarity and cognitive-load proxies, Persuasion cues present, Dark-pattern risk signals) restano la denominazione di studio: la tabella di 3.1 li affianca.

---

## 2. Modello concettuale

Il catalogo non parte da una metrica per volta ma da costrutti teorici, ciascuno con una domanda precisa: che cosa si può osservare da una pagina, e con quale forza di evidenza.

### 2.1 Modello comportamentale di Fogg: B = MAP

Un comportamento avviene quando Motivazione, Abilità e Prompt convergono nello stesso momento (behaviormodel.org [V]). Per Fogg l'abilità è "semplicità" ed è determinata dalla risorsa più scarsa tra sei fattori: tempo, denaro, sforzo fisico, sforzo mentale, deviazione sociale, non routine. La catena è forte quanto l'anello più debole (la ricerca tagga il modello [V] e i dettagli [S] o [M]). I tipi di prompt (spark, facilitator, signal) vengono dall'articolo del 2009 [M].

B=MAP è un'impalcatura utile, non un'equazione stimata. Il catalogo la usa così.

| Componente | Che cosa si osserva | Dove sta nel catalogo | Evidenza |
|---|---|---|---|
| **Motivazione** | Non osservabile. Solo l'offerta di motivatori: proposta di valore, riprova sociale, scarsità e urgenza genuine, reciprocità, autorità | MPI, più `CCL.VALUE_PROP_CLARITY` | Debole o moderata |
| **Abilità** | Frizione osservabile: passi e sforzo, campi, account obbligatorio, dimensione dei target, errori | FAI per tempo, sforzo fisico e mentale; PTI per il denaro (scelta del catalogo); PERF per la robustezza | Forte per la frizione del checkout (Baymard); moderata per la mappa sui sei fattori, che è concettuale |
| **Prompt** | Salienza della CTA: una CTA dominante per fase, above the fold, contrasto, etichetta specifica | `CCL.CTA_SALIENCE`, `CCL.PRIMARY_CTA_COUNT`, `FAI.PDP_CTA_ABOVE_FOLD` | Debole o moderata: soglie convenzionali di pratica, nessun effetto causale da affermare |

Fattori di Fogg senza KPI dedicato in v1: la deviazione sociale (campi invasivi come telefono, data di nascita, codice fiscale non necessario) è un'evidenza debole e si limiterebbe a un conteggio; la non routine (aderenza alle convenzioni: logo che porta alla home, icona carrello in testata) pure, a peso leggero. Il numero di passi del checkout non è un KPI v1: Baymard riporta una media di 5,1 passi (2024) ma sostiene che i campi pesano sull'usabilità molto più dei passi.

**Perché la media geometrica.** Poiché B=MAP richiede tutti e tre gli elementi, l'aggregazione deve essere non compensativa: un sotto-indice debole non può essere comprato interamente da uno forte (sezione 3.4).

### 2.2 I principi di Cialdini (offerta di motivatori)

I sette principi sono reciprocità, scarsità, autorità, coerenza, simpatia, riprova sociale e unità (influenceatwork.com [V]). Regola del catalogo: la versione **genuina e verificabile** di ogni segnale si valuta in MPI; la versione **falsa o ingannevole** si valuta solo in DPR, mai come credito.

| Principio | Segnali in pagina | KPI v1 | Evidenza |
|---|---|---|---|
| Riprova sociale | Voto e numero di recensioni (JSON-LD `aggregateRating` più DOM), recensioni recenti, acquisto verificato, foto | `TRI.REVIEWS_PRESENT`, `TRI.REVIEW_COUNT`, `TRI.RATING_BAND`; `MPI.SOCIAL_PROOF_RICH` (giudicato) | Moderata o forte, osservazionale. Spiegel: cinque recensioni alzano la probabilità di acquisto del 270% rispetto a nessuna, con picco tra 4,0 e 4,7 stelle e rendimenti decrescenti dopo le prime cinque [S] |
| Scarsità | Disponibilità in `Offer.availability` (`LimitedAvailability`), date di fine promozione dichiarate | `MPI.SCARCITY_SIGNALS` (accreditato solo se `DPR.FAKE_LOW_STOCK` non è scattato, sezione 3.3) | Moderata. Barton et al. 2022, meta-analisi di 131 studi e 416 effect size [S]: la scarsità di domanda funziona meglio per beni utilitaristici, quella di offerta per esperienze, quella di tempo per alto coinvolgimento |
| Urgenza (scarsità di tempo) | Scadenza dichiarata | `MPI.URGENCY_SIGNALS` (accreditato solo se `DPR.COUNTDOWN_RESET` non è scattato, sezione 3.3) | Moderata. Mathur distingue una scadenza dichiarata (può essere genuina) da "sta per finire" senza scadenza (informazione nascosta) [V] |
| Reciprocità | Omaggi, campioni, sconto iscrizione, resi gratuiti | `MPI.RECIPROCITY` | Debole o moderata (solo proxy) |
| Autorità | Certificazioni, esperti, stampa, identificativi regolatori | `MPI.AUTHORITY` (giudicato) | Debole o moderata |
| Coerenza, simpatia, unità | Lista desideri, carrello salvato, persone reali, community | Nessun KPI v1 | Debole |

Un segnale DPR "scatta" quando la sua confidenza aggregata sul sito è almeno 0,5 (`dpr_fired_confidence` in `anchors.json`): solo allora il credito MPI corrispondente è ritirato.

### 2.3 Carico cognitivo, scelta e information scent

**Legge di Hick.** Il tempo di decisione cresce con numero e complessità delle scelte (Hick e Hyman, 1952-53) [V], ma fu stabilita con compiti semplici stimolo-risposta: il trasferimento allo shopping è debole o moderato. **Legge di Miller (7±2):** non si usa come soglia rigida; lavori successivi (Cowan) indicano circa 4 chunk [V]. Evidenza debole.

**Choice overload: evidenza mista.**

| Studio | Risultato |
|---|---|
| Iyengar e Lepper (2000) [V] | Tra chi si fermava allo stand, comprò il 3% con 24 gusti di marmellata contro il 30% con 6; ma con 24 gusti si fermava più gente (60% contro 40%) |
| Scheibehenne, Greifeneder e Todd (2010) [S] | 63 condizioni da 50 esperimenti, N = 5.036: effetto medio praticamente nullo, con alta varianza |
| Chernev, Böckenholt e Goodman (2015) [S] | 99 osservazioni, N = 7.202: l'effetto diventa significativo date quattro condizioni moderatrici (complessità dell'insieme, difficoltà del compito, incertezza sulle preferenze, obiettivo di minimizzare lo sforzo) |

Conseguenza di misura: nessuna penalità per l'assortimento grande in sé, ma solo per l'assortimento grande **senza strumenti di decisione** (filtri, ordinamento). È la logica di `CCL.CHOICE_SUPPORT`.

**Information scent** (Pirolli e Card 1999 [S]): gli utenti seguono segnali prossimali, come le etichette dei link, verso il valore atteso. Si misura con la quota di etichette generiche (`CCL.INFO_SCENT_GENERIC`), la presenza di breadcrumb e di ricerca con autocomplete.

**Gerarchia visiva e schemi F/Z.** Lo studio NN/g sull'F-pattern è del 2006 (232 utenti) e descrive una "forma approssimativa"; il seguito del 2017 documenta schemi diversi [V/S]. Uso ammesso: euristica di collocazione (proposta di valore e CTA primaria in alto a sinistra o nel primo viewport). Non si valuta la "conformità a una F". Evidenza debole o moderata.

**Proxy di carico cognitivo.** La ricerca ne elenca diversi: parole above the fold, font e colori distinti, elementi interattivi per viewport, caroselli e animazioni, popup per sessione, complessità visiva, leggibilità. La v1 ne usa un sottoinsieme: CTA in competizione, intestazioni, leggibilità, complessità visiva, overlay.

### 2.4 Fiducia e rischio percepito

**Linee guida di credibilità di Stanford** [V]. La base è uno studio su oltre 4.500 persone in tre anni, su percezioni, non su acquisti. Le dieci linee guida: verificare l'accuratezza, mostrare un'organizzazione legittima, evidenziare la competenza, mostrare le persone dietro il sito, rendere facile il contatto, design professionale, usabilità e utilità, aggiornamenti regolari, limitare i contenuti promozionali (inclusi i pop-up), eliminare gli errori.

**Teoria prominence-interpretation di Fogg** [S]: il giudizio di credibilità dipende dal fatto che un elemento venga notato (prominenza) e da come viene interpretato. Si misurano entrambe: prominenza come presenza nel viewport su PDP e checkout, interpretazione come segnale riconoscibile e reale. La v1 misura soprattutto la presenza e solo in parte la prominenza.

**Numeri di Baymard su fiducia e rischio** (lista abbandoni [V], quote di chi abbandona, escluso chi "stava solo guardando", cioè il 42%): 19% non si fida del sito per i dati della carta, 13% trova insoddisfacente la politica di reso, 9% trova pochi metodi di pagamento, 20% trova la consegna troppo lenta.

### 2.5 Trasparenza dei prezzi, ancoraggio e avversione alla perdita

**Costi nascosti.** Secondo Baymard (lista abbandoni [V]) il 40% abbandona per "costi extra troppo alti" (spedizione, tasse, commissioni) e il 12% perché non riesce a vedere o calcolare il totale in anticipo. Il 40% esclude chi "stava solo guardando" (42% di chi abbandona). Altri sondaggi riportano cifre più alte su un denominatore diverso: 47% (2023) e 48% (febbraio 2024, n = 1.012 adulti USA) [S]; una sintesi di ricerca cita anche un 39% per il 2025 che non è stato confermato. Si cita il 40% della pagina Baymard dichiarando il denominatore.

**Ancoraggio e framing di perdita.** Prezzi barrati, "risparmi X euro", opzioni esca con badge "più popolare", barre di spedizione gratuita. Si contano come persuasione solo se il prezzo di riferimento è verificabilmente legittimo. La v1 non accredita questi segnali in MPI e si limita a controllare la dicitura Omnibus accanto al prezzo barrato (`PTI.STRIKETHROUGH_LOWEST30`). L'ancoraggio è forte in laboratorio; l'ordine di grandezza reale dell'avversione alla perdita è dibattuto [M].

**Regola UE sulle riduzioni di prezzo.** Art. 6a della Direttiva sull'indicazione dei prezzi (introdotto dalla direttiva Omnibus), applicabile dal 28 maggio 2022 [S]: il prezzo precedente è il più basso applicato nei 30 giorni precedenti. La sentenza CGUE C-330/23 (Aldi Süd, 26 settembre 2024) ha confermato che la riduzione percentuale va calcolata su quel prezzo [S]. Il controllo lessicale di "prezzo più basso degli ultimi 30 giorni" è deterministico; verificare lo storico reale richiede crawl ripetuti nel tempo, cioè un monitoraggio, non un audit a visita singola.

### 2.6 Dark pattern come segnali negativi

**Tassonomie.**

- **Mathur et al. 2019** [V]: circa 11.000 siti di shopping, 53.180 pagine prodotto; 1.818 istanze su 1.254 siti (circa 11,1%); 15 tipi in 7 categorie; i conteggi sono limiti inferiori. 234 istanze ingannevoli su 183 siti; 22 entità terze che vendono dark pattern come servizio. Caratteristiche: asimmetrici, occulti, ingannevoli, che nascondono informazioni, restrittivi.
- **Brignull, deceptive.design** [V]: altri tipi, tra cui Nagging, Preselection, Fake Urgency, Fake Scarcity, Fake Social Proof, Comparison Prevention, Disguised Ads.
- **FTC, "Bringing Dark Patterns to Light" (settembre 2022):** il rapporto esiste [V per l'esistenza]; i dettagli della tassonomia non sono stati estratti.

**Perché DPR è solo penalità.** Luguri e Strahilevitz (2021) [V]: accettazione di un servizio dubbio 11,3% senza dark pattern, 25,8% con pattern lievi, 41,9% con pattern aggressivi; i meno istruiti sono più suscettibili ai pattern lievi; solo i pattern aggressivi hanno prodotto un contraccolpo misurabile. Un negozio può quindi andare bene sulla conversione immediata e portare rischi di fiducia e normativi: DPR non si premia mai.

**Protocollo di rilevazione** (replica i metodi di Mathur: crawler, mutation observer, visite ripetute). La colonna "KPI v1" collega ogni test al catalogo.

| Pattern | Controllo deterministico | Test di inganno | KPI v1 |
|---|---|---|---|
| Countdown | Formato orario più nodo di testo che muta ogni secondo | Ricarica in un contesto fresco, dopo almeno 10 s. Ingannevole se il timer si azzera con la stessa offerta; il secondo criterio di Mathur, l'offerta ancora valida a scadenza, non è verificato in v1 (157 istanze ingannevoli su 140 siti) | `DPR.COUNTDOWN_RESET` |
| Scorte basse | Regex ("solo N rimasti", "ultimi N pezzi") | Confronto tra visite e con `availability`. Mathur: 16 siti ingannevoli su 17 decrementavano a tempo, 1 in modo casuale; alcuni usavano script di terze parti. Si segnala anche il messaggio su quasi tutte le PDP | `DPR.FAKE_LOW_STOCK` |
| Sneak into basket e opzione a pagamento pre-selezionata | Diff tra righe del carrello e articoli aggiunti; `input:checked` vicino a parole chiave di assicurazione, garanzia, donazione | Deterministico | `DPR.SNEAK_INTO_BASKET`, `DPR.PRECHECKED_PAID_ADDONS` |
| Asimmetria del banner di consenso | Rifiuto sul primo livello? Pari prominenza? Rapporto di area e di contrasto tra "accetta" e "rifiuta" | Deterministico. EDPB: rifiutare non deve essere più difficile che accettare [S]. Nouwens et al.: togliere l'opt-out dalla prima pagina ha alzato il consenso di 22-23 punti percentuali [S] | `DPR.CONSENT_ASYMMETRY` |
| Nagging | Overlay e dialoghi che tornano dopo essere stati chiusi, tra le pagine del funnel e dopo un ricarico | Deterministico per sessione; exit-intent e popup temporizzati non sono rilevati in v1 | `DPR.NAGGING_OVERLAYS` |
| Confirmshaming, trick question, abbonamento nascosto | Estrazione del testo delle etichette di rifiuto, delle caselle e dei passaggi su addebiti ricorrenti | Non applicabile | LLM binario con citazione: `DPR.CONFIRMSHAMING`, `DPR.TRICK_QUESTIONS`, `DPR.HIDDEN_SUBSCRIPTION` |

**Non coperti in v1** (dalla ricerca): messaggio di attività o alta domanda ("N persone stanno guardando", test di revisita: Mathur 29 istanze ingannevoli su 20 siti), messaggio a tempo limitato senza scadenza (informazione nascosta per definizione), visual interference e pressured selling (variante più costosa pre-selezionata), costi nascosti come meccanismo (sono in PTI), registrazione forzata (FAI), difficoltà di disdetta (richiede un flusso autenticato, di norma fuori ambito).

**Accuratezza dei rilevatori automatici.** AidUI, basato su screenshot, riporta P 0,66, R 0,67, F1 0,65 su 10 tipi di pattern, con alcuni tipi sopra 0,82 [S]. Esiste uno studio di GPT-4.1 contro esperti di dark pattern, ma i numeri non sono stati estratti [S]. Di qui la preferenza per test DOM e comportamentali rispetto a rilevatori visivi o LLM.

**Quadro normativo** (serve a motivare l'etichetta "rischio"; non è consulenza legale).

| Norma | Contenuto sintetico | Stato della fonte |
|---|---|---|
| Sweep CPC della Commissione UE (30 gennaio 2023) | 399 negozi esaminati; 148 usavano almeno un dark pattern; 42 timer finti, 54 scelte guidate, 70 informazioni nascoste, 23 informazioni nascoste sugli abbonamenti | [S] |
| Direttiva sulle pratiche commerciali sleali (UCPD), Allegato I | La lista nera include l'affermare falsamente che un prodotto è disponibile solo per un tempo molto limitato | [M] |
| Direttiva Omnibus, art. 6a Direttiva sull'indicazione dei prezzi | Prezzo di riferimento = più basso dei 30 giorni precedenti, dal 28 maggio 2022 | [S] |
| Digital Services Act, art. 25 | Vieta interfacce ingannevoli o manipolative per le piattaforme online; non si applica dove UCPD o GDPR già coprono la pratica, quindi riguarda soprattutto i marketplace | [S] |
| Direttiva (UE) 2023/2673 | Funzione di recesso facile da trovare per i contratti a distanza online, applicabile dal 19 giugno 2026: ora verificabile per gli shop UE | [S] |
| Digital Fairness Act | Proposta attesa per l'autunno 2026 su dark pattern e abbonamenti trappola; non è confermato che sia stata pubblicata | [S] |
| European Accessibility Act | Applicabile dal 28 giugno 2025, copre l'e-commerce, esenta le microimprese: rilevante per `FAI.A11Y_BASIC` e `FAI.TARGET_SIZE_*` | [S] |

Il rapporto parla sempre di "segnali di rischio dark pattern" (in inglese dark-pattern risk signals), mai di violazioni o di esiti giuridici.

### 2.7 Estetica, emozione e leggibilità

**Prime impressioni.** I giudizi si formano entro 50 ms (Lindgaard et al. 2006 [S]); l'estetica percepita ha influenzato l'usabilità percepita dopo l'uso, mentre l'usabilità reale no (Tractinsky et al. 2000 [S]; sintesi in lawsofux.com). Evidenza forte sugli effetti di percezione, debole o moderata sugli effetti di conversione.

**Reinecke et al., CHI 2013** [V]: 450 siti, valutazioni a 500 ms di esposizione.

| Grandezza | Adattamento del modello | Predittori principali |
|---|---|---|
| Colorfulness percepita | R² = 0,78 | Colorfulness di Hasler-Süsstrunk (il più forte, β = 0,58), saturazione, numero di aree immagine, foglie di quadtree, area di testo e non testo |
| Complessità visiva percepita | R² = 0,65 | Scomposizione in quadtree (numero di foglie), scomposizione spaziale |
| Appeal visivo dopo 500 ms | R² corretto = 0,48 | Modelli più dati demografici (circa metà della varianza) |

Uso nel catalogo: si calcolano dallo screenshot gli stessi tipi di feature come proxy di complessità e colorfulness (`CCL.VISUAL_COMPLEXITY`). Colorfulness di Hasler-Süsstrunk [M]: M = √(σ²_rg + σ²_yb) + 0,3·√(μ²_rg + μ²_yb). I modelli predicono complessità e colorfulness **percepite**, non la conversione, e le preferenze variano con demografia e cultura (Reinecke e Gajos 2014 [S]). Si segnalano solo gli estremi; non si definisce una complessità "ideale".

Cautela sul composito: la colorfulness di Hasler-Süsstrunk è il predittore più forte della colorfulness percepita (R² = 0,78), non della complessità percepita, il cui modello (R² = 0,65) poggia sulla scomposizione in quadtree e su quella spaziale. Nel composito di `CCL.VISUAL_COMPLEXITY` la colorfulness è quindi un proxy debole di complessità, e il KPI resta provvisorio con peso 0,5.

**Leggibilità (italiano).** Indice Gulpease [S]: 89 − 10·(lettere/parole) + 300·(frasi/parole). Soglie: sotto 80 testo difficile per chi ha la licenza elementare, sotto 60 per la licenza media, sotto 40 per il diploma superiore. Il testo legale ha per natura valori più bassi e richiederebbe un'ancora separata; il tono (pressione contro informazione, empatia, gergo) sarebbe una rubrica LLM di evidenza debole e non è un KPI v1. Evidenza: forte come misura di leggibilità, debole come predittore di engagement.

---

## 3. Gli indici

### 3.1 Panoramica

Ogni KPI appartiene a **un solo** sotto-indice (una sola "casa") oppure alla penalità DPR. Gli indici vanno da 0 a 100; per i sei sotto-indici positivi più alto è meglio, DPR è un rischio.

| Indice | Nome nel rapporto | Nome di ricerca | Peso nell'ERS | KPI | Contenuto |
|---|---|---|---|---|---|
| **PERF** | Prestazioni | Performance | 18 | 16 | Velocità percepita, stabilità del layout, peso e richieste, terze parti, errori, risposta alle azioni |
| **FAI** | Attrito previsto | Predicted friction (Friction/Ability) | 22 | 29 | Ricerca, listing, pagina prodotto, carrello, campi del checkout, ospite, account forzato, target, accessibilità base, overlay; dal journey: successo, azioni, tempo-sito, dead click, rage, backtrack, Lostness, navigazioni inattese |
| **TRI** | Segnali di fiducia | Trust signals observable | 20 | 13 | HTTPS, contatti, identificativo legale, politiche, dati strutturati di resi e spedizione, metodi di pagamento, recensioni, tempi di consegna; giudicata la chiarezza dei resi |
| **PTI** | Trasparenza di prezzi e costi | Price-transparency signals | 15 | 8 | Prezzo visibile e coerente, spedizione e IVA prima del checkout, delta di prezzo nel funnel, dicitura Omnibus, costi non spiegati |
| **CCL** | Chiarezza e carico cognitivo | Clarity and cognitive-load proxies | 15 | 9 | Salienza e numero delle CTA, supporto alla scelta, information scent, leggibilità, intestazioni, etichette dei form, complessità visiva; giudicata la proposta di valore |
| **MPI** | Leve di persuasione genuine | Persuasion cues present | 10 | 5 | Scarsità e urgenza genuine, reciprocità; giudicate autorità e riprova sociale ricca |
| **DPR** | Segnali di rischio dark pattern | Dark-pattern risk signals | penalità fino al 30% dell'ERS | 9 | Timer che si azzera, scorte finte, sneak into basket, opzioni pre-selezionate, asimmetria del consenso, overlay insistenti; giudicati confirmshaming, trick question, abbonamento nascosto |

Totale: 16 + 29 + 13 + 8 + 9 + 5 + 9 = 89 KPI.

### 3.2 Pesi 18 / 22 / 20 / 15 / 15 / 10 e razionale

I pesi sommano 100. Sono **prior, non stime**, e vengono dalla ricerca di psicologia (sezione 2.1), che li propone come miscela del prior ricavato dalle cause di abbandono di Baymard, della struttura di B=MAP di Fogg e dell'evidenza sulla velocità (Deloitte Digital e 55 per Google); il catalogo li adotta così come sono. Saranno sostituiti da pesi stimati solo dopo la calibrazione (sezione 7), se ci saranno abbastanza siti; in caso contrario restano i prior. L'analisi di sensibilità dei pesi è fase 2 (sezioni 3.5 e 8).

**Mappatura Baymard delle cause di abbandono sui sotto-indici** (lista abbandoni [V]: media di abbandono del carrello 70,22% su 50 studi; quote tra chi abbandona, a scelta multipla quindi con somma oltre 100, escluso chi "stava solo guardando"; la pagina non riporta data né dimensione del campione).

| Sotto-indice | Cause mappate (quota %) | Somma | Quota normalizzata | Peso v1 |
|---|---|---|---|---|
| TRI | Sfiducia sui dati della carta 19 + politica di reso 13 + pochi metodi di pagamento 9 + consegna lenta 20 | 61 | circa 37% | 20 |
| PTI | Costi extra troppo alti 40 + totale non visibile 12 | 52 | circa 31% | 15 |
| FAI | Creazione account obbligatoria 18 + checkout lungo o complicato 17 | 35 | circa 21% | 22 |
| PERF | Errori o crash del sito 17 | 17 | circa 10% | 18 |
| CCL | non coperto (la tabella riguarda solo il checkout) | | | 15 |
| MPI | non coperto | | | 10 |

Il totale delle cause mappate è 165 (61 + 52 + 35 + 17). La tabella copre solo il checkout ed esclude CCL, MPI e DPR; per questo i pesi non coincidono con le quote.

Razionale per sotto-indice (ragionamento di progetto, non evidenza).

- **FAI 22, il peso più alto.** In B=MAP l'abilità è la componente osservabile e la ricerca ha evidenza forte sulla frizione del checkout (Baymard). Contiene inoltre segnali che la tabella dei motivi di abbandono non vede (ricerca, listing, pagina prodotto, journey). Il prior Baymard da solo darebbe circa 21%.
- **TRI 20.** Il prior Baymard (circa 37%) è il più alto, ma qui si misurano soprattutto presenze (contatti, politiche, loghi) e non la percezione reale di fiducia, e la causa più pesante mappata (consegna lenta, 20%) è osservabile solo come dichiarazione dei tempi.
- **PTI 15.** Il prior Baymard è il secondo (circa 31%): i costi extra sono la prima causa di abbandono tra quelle mappate (40%, escluso chi "stava solo guardando"). Il peso 15 è quello della miscela di ricerca e non deriva dalla tabella. Gli stadi non raggiunti non si trattano con il peso: i KPI relativi escono dal calcolo e dalla copertura (sezione 3.5).
- **PERF 18.** Baymard attribuisce solo circa 10% (errori e crash). Il peso sale per due ragioni: l'evidenza sulla velocità (Deloitte Digital e 55 per Google, 2020 [S]: 0,1 s di miglioramento = +8,4% di conversione retail su 37 marchi e oltre 30 milioni di sessioni; studio osservazionale promosso da Google, vedi 4.1) e il fatto che le sue metriche sono misure strumentali dirette, con soglie pubblicate per i Core Web Vitals e nessun KPI giudicato (resta la variabilità di laboratorio: sezione 5.7).
- **CCL 15.** Salienza della CTA e carico di scelta sono condizioni di B=MAP (prompt) e di qualità percepita, ma le soglie sono convenzioni di pratica, quindi evidenza debole o moderata e peso medio.
- **MPI 10, il peso più basso.** La motivazione non è osservabile, solo l'offerta di motivatori; il sotto-indice premia solo versioni genuine e dipende in parte da giudizi LLM.

### 3.3 Normalizzazione a 0-100

1. **Funzione piecewise-lineare per KPI**, con punti `[valore, punteggio]` in `anchors.json`. Dove esiste una soglia pubblicata (Core Web Vitals, WCAG, Baymard, Gulpease, Spiegel, Smith) i punti "buono" e "scarso" vengono da lì: il valore "buono" vale 100 e il punto intermedio pubblicato (la soglia "scarso", oppure la media o il valore efficace riportati dalla fonte) vale tra 40 e 70 secondo il KPI (50 per i Core Web Vitals). Il punto a 0 è l'estremo: per i Core Web Vitals è il doppio della soglia "scarso" e questa è una scelta editoriale. Dove non esiste una soglia pubblicata l'ancora è editoriale, marcata `provisional`, in attesa di percentili su un corpus di riferimento (fase 2). Valori oltre il primo o l'ultimo punto sono tagliati ai punteggi estremi.
2. **Ancore versionate** (`anchors.v2`; v2 = v1 con `FAI.CHECKOUT_FIELDS` e `CCL.READABILITY` segnati Provv., nessun punto o peso cambiato: i punteggi restano confrontabili con v1). Non si rinormalizza per batch, così i punteggi restano confrontabili nel tempo. Un percentile rispetto al corpus, come fa SUPR-Q, è un'opzione di fase 2.
3. **Booleani** valgono 100 o 0; i **negativi** sono invertiti (per esempio `FAI.FORCED_ACCOUNT`: vero = 0). Le **etichette chiuse** dei giudizi LLM usano una mappa esplicita per KPI (la ricerca proponeva 0, 1, 2 mappati su 0, 50, 100; la v1 adatta i valori per KPI, per esempio `vague` = 40).
4. **Non applicabile non è 0.** Un'osservazione con `assessed = false` e motivo `not_applicable...` esce dalla copertura (per esempio prodotto senza varianti, nessun prezzo barrato, nessun campo di form): vale solo per ciò che esiste e non si applica, mai per uno stadio non raggiunto, che è "non valutabile" (sezione 3.5).
5. **Aggregazione tra pagine e profili** secondo `kpis.py`: mediana pesata (`wmedian`) con peso doppio per PDP e PLP, mediana, massimo, minimo, media, somma, `any`, `all`, `first` (il primo valore che le ancore sanno valutare) e `best` (il valore con il punteggio di ancora più alto, a parità il primo: lo usano i quattro KPI giudicati a livello di sito, che premiano la pagina più chiara raggiunta). Un KPI osservato su due o più profili è aggregato per profilo con la sua regola (le righe senza profilo contano in ogni profilo) e il valore complessivo è quello del profilo che ottiene il punteggio più basso sulle ancore: un acquirente usa un dispositivo, non l'unione di due. I profili che le ancore non sanno valutare sono ignorati (se nessuno lo è, le righe si uniscono) e le confidenze DPR si uniscono sempre con il massimo; il rapporto indica il profilo peggiore solo se è strettamente peggiore degli altri. Per `sum`, `any` e gli altri aggregati vale la stessa regola; per un KPI `any` un profilo il cui falso poggia su una pagina non osservata è escluso (3.5, regola g).
6. **Dentro un sotto-indice:** media pesata dei punteggi dei KPI valutati, con i pesi dei KPI di `anchors.json` (da 0,5 a 3; peso 0 per i KPI solo informativi) rinormalizzati sui KPI valutati. I pesi dei KPI sono una scelta editoriale per ogni KPI, pubblicato o provvisorio: nessuna fonte li fornisce.
7. **Credito MPI condizionato.** Un segnale DPR "scatta" quando la sua confidenza aggregata sul sito è almeno 0,5 (`dpr_fired_confidence` in `anchors.json`). Se scatta `DPR.FAKE_LOW_STOCK` o `DPR.COUNTDOWN_RESET`, `MPI.SCARCITY_SIGNALS` o `MPI.URGENCY_SIGNALS` sono punteggiati come se il segnale fosse assente (valore 0, cioè 30 con le ancore v2), con motivo `credit_withheld:<id DPR>`: nessun credito a un segnale ingannevole, la cui penalità vive solo in DPR. Un segnale DPR non valutato (per esempio test di inganno non eseguito) non ritira il credito.

I punti delle ancore sono riportati per KPI nel catalogo (sezione 4).

### 3.4 Formule: sotto-indice, DPR, ERS

```
S_i     = Σ_k w_k · n_k / Σ_k w_k                      k = KPI valutati del sotto-indice i, n_k in 0..100
DPR     = 100 · (1 − Π_p (1 − sev_p · conf_p))          noisy-OR sui segnali di rischio
ERS_raw = exp( Σ_i W_i · ln(max(S_i, 10)) / Σ_i W_i )   media geometrica pesata, floor 10
ERS     = ERS_raw · (1 − 0.30 · DPR / 100)
```

con W = FAI 22, TRI 20, PTI 15, CCL 15, PERF 18, MPI 10. La somma sui sotto-indici considera solo quelli valutabili (i pesi sono rinormalizzati); le regole di pubblicazione della sezione 3.5 impediscono di pubblicare un ERS con copertura insufficiente.

**Perché geometrica e con floor 10.** La media geometrica è non compensativa: un sotto-indice debole non è pienamente riscattato da uno forte, in coerenza con B=MAP. Il manuale OECD/JRC sugli indicatori compositi discute il compromesso tra compensabilità e non compensabilità [S]. Il floor a 10 evita ln(0) e impedisce che un singolo sotto-indice nullo azzeri tutto. Il rapporto indica sempre il **fattore limitante**, cioè il sotto-indice più basso, e mostra il profilo con una barra per sotto-indice e una tabella di confronto per profilo (la ricerca proponeva un radar). Per ogni sotto-indice elenca anche fino a 3 KPI limitanti (`limiting_kpis` in `anchors.json`): i KPI valutati con punteggio sotto 100, dal più basso; a parità, dal peso maggiore.

**Esempio numerico illustrativo** (valori inventati per mostrare il calcolo, non dati di uno shop reale).

| Caso | FAI | TRI | PTI | CCL | PERF | MPI | Media aritmetica pesata | ERS_raw (geometrica) |
|---|---|---|---|---|---|---|---|---|
| A | 70 | 80 | 60 | 75 | 55 | 40 | 65,6 | 64,3 |
| B (come A, PERF = 20) | 70 | 80 | 60 | 75 | 20 | 40 | 59,3 | 53,6 |

Nel caso B un solo sotto-indice debole costa alla media aritmetica 6,3 punti e a quella geometrica 10,7: è l'effetto non compensativo.

**DPR e severità.** La severità è il danno attribuito al segnale se il pattern è confermato (valori in `kpis.py`: 0,9, 0,6, 0,3); l'incertezza del rilevatore non la abbassa e vive solo nella confidenza, che è la forza del test di inganno (0..1; per i segnali giudicati "presente" è 0,8 moltiplicato per la quota di accordo tra i giudici: 0,8 se concordano tutti e tre i campioni di Claude, circa 0,53 se concordano in due, 0,8 per il campione unico di Jev). L'effetto sull'ERS di un segnale singolo:

| Segnale | Severità x confidenza | DPR | Fattore sull'ERS |
|---|---|---|---|
| Timer che si azzera, confermato | 0,9 x 1,0 | 90 | 0,73 |
| Opzione a pagamento pre-selezionata, confermata | 0,6 x 1,0 | 60 | 0,82 |
| Asimmetria del consenso, confermata | 0,3 x 1,0 | 30 | 0,91 |
| Confirmshaming "presente", accordo totale | 0,6 x 0,8 | 48 | 0,856 |
| Timer che si azzera e sneak into basket, entrambi confermati | 0,9 x 1,0 e 0,9 x 1,0 | 99 | 0,703 |

Il fattore minimo è 0,70, quindi la penalità non supera il 30%. Nell'esempio A, un timer confermato porta l'ERS da 64,3 a circa 46,9. La somma satura in fretta: un solo segnale confermato consuma gran parte del budget del 30%, quindi la gradazione tra uno e cinque segnali si legge dal punteggio DPR e dall'elenco dei segnali, non dall'ERS. I valori della tabella si riproducono con `scoring.dpr` e `scoring.ers`.

Attenzione: i prior della ricerca per le severità erano più bassi (0,35-0,5 per pattern ingannevoli confermati, 0,10-0,15 per pattern presenti ma non provati). I valori v1 (0,9 / 0,6 / 0,3) sono più alti e restano in v1 perché sono quelli del piano approvato (i sei segnali deterministici), del piano di architettura (i tre giudicati) e del registro `kpis.py` (`anchors.json` non contiene severità e rimanda a `kpis.py`), la penalità è limitata (fattore minimo 0,70) e la ricerca stessa ammette per un timer ingannevole confermato un trattamento simile a un tetto (scelta di progetto, non risultato empirico). Il coefficiente 0,30 della penalità è anch'esso un prior.

**Piano v2 per le severità** (non in v1; ipotesi da riesaminare). Dopo il primo audit di correlazione, oppure dopo almeno 20 audit reali (una soglia scelta dal progetto, non una fonte della ricerca), le severità si riportano verso le fasce della ricerca (ipotesi del catalogo: 0,5 per timer e sneak into basket, 0,35 per scorte finte e opzioni pre-selezionate, 0,15 per consenso e nagging) e passano da `kpis.py` a `anchors.json` come chiave opzionale `severity` per ogni DPR, con ripiego su `kpis.py`, così che tutti i parametri di scoring siano dati versionati.

Le fasce della ricerca sono due. La classe 0,35-0,5 elenca i pattern ingannevoli confermati: timer che si azzera, scorte finte, sneak into basket, opzione a pagamento pre-selezionata. La classe 0,10-0,15 elenca i pattern presenti ma non provati: timer presente, scorta bassa presente, confirmshaming, nagging. La ricerca non assegna alcuna classe all'asimmetria del consenso: lo 0,15 che le dà l'ipotesi è una collocazione del catalogo, e solo il nagging è nominato nella fascia 0,10-0,15. Anche la divisione della prima classe tra 0,5 e 0,35 è del catalogo.

Per i tre segnali giudicati vale lo stesso principio: **severità = danno se il pattern è confermato; l'incertezza del rilevatore vive solo nella confidenza.** I rilevatori giudicati pagano già l'essere basati su LLM con la confidenza 0,8 x accordo tra i giudici: abbassare anche la severità perché il rilevatore è solo LLM sarebbe uno sconto doppio, e per questo un valore piatto di 0,35 per i tre segnali è scartato. La classe "presente ma non provato" della ricerca (0,10-0,15) elenca timer presente, scorta bassa presente, confirmshaming e nagging. Il catalogo non la applica in blocco ai tre segnali giudicati: li gradua per danno se il pattern è confermato. Confirmshaming resta al vertice della fascia della ricerca (0,15) perché non produce un danno monetario: del valore della ricerca il catalogo adotta il numero e non il motivo, dato che l'incertezza vive già nella confidenza. Trick question (0,35) e abbonamento nascosto (0,5) la ricerca non li nomina: producono un'iscrizione o un consenso non voluti, o un addebito ricorrente nascosto, e si collocano nella fascia dei pattern ingannevoli confermati. Valori ipotizzati:

| Segnale | v1 | v2 (ipotesi) | Motivo |
|---|---|---|---|
| Confirmshaming (`DPR.CONFIRMSHAMING`) | 0,6 | 0,15 | La ricerca lo elenca nella fascia 0,10-0,15, insieme a timer presente, scorta bassa presente e nagging; il catalogo ne tiene il vertice perché non produce un danno monetario; in termini di Luguri e Strahilevitz si legge come pattern lieve (lettura del catalogo) |
| Trick question (`DPR.TRICK_QUESTIONS`) | 0,6 | 0,35 | Non nominato dalla ricerca. Produce un'iscrizione o un consenso non voluti; estremo basso della fascia dei pattern ingannevoli confermati (lettura del catalogo) |
| Abbonamento nascosto (`DPR.HIDDEN_SUBSCRIPTION`) | 0,9 | 0,5 | Non nominato dalla ricerca. Famiglia "sneaking" di Mathur insieme a sneak into basket [V]; danno monetario; sweep UE su 23 negozi [S]; stesso valore di timer e sneak nell'ipotesi |

Caso peggiore con accordo totale e valori v1: 0,9 x 0,8 = 0,72, cioè DPR 72 e fattore 0,78 sull'ERS (nell'ipotesi v2 sarebbe 0,5 x 0,8 = 0,4, DPR 40 e fattore 0,88). I valori v2 sono ipotesi da riesaminare dopo l'audit di correlazione o dopo almeno 20 audit reali.

**Blocchi critici.** La ricerca propone di marcare con un flag visibile, o con un tetto, i casi critici (checkout irraggiungibile, timer ingannevole confermato) e precisa che sono scelte di progetto, non risultati empirici. La v1 non applica tetti: li mostra tramite fattore limitante e risk signals.

### 3.5 Copertura, confidenza, gradi e pubblicazione

- **Copertura per sotto-indice** = peso dei KPI valutati / peso dei KPI applicabili. **Copertura complessiva** = Σ W_i · copertura_i / Σ W_i.
- **Applicabilità.** I KPI di journey sono "non applicabili" se non è stato eseguito un journey, e i KPI giudicati se i giudizi non sono stati richiesti: in quel caso non abbassano la copertura. La copertura da sola non dice quali famiglie di KPI erano attive, quindi il rapporto mostra accanto alla confidenza una riga di **ambito**, letta da `scores.overall.context` (`journey` e `judged`) e dalla copertura DPR: `Ambito: audit deterministico · senza journey · senza giudizi LLM · rischio dark pattern valutato 6/6` (oppure "con journey", "con giudizi LLM", "rischio dark pattern non valutato"; per una run di solo journey "journey senza audit"). La riga compare nel rapporto HTML, nei riepiloghi di `score_run` e `get_report` (`scope`) e, con la chiave `context`, in `headline` di `report.json`.
- **Una pagina non raggiungibile è "non valutabile", mai 0.** I KPI di quello stadio escono dal calcolo con il motivo registrato e la copertura scende di conseguenza. I motivi sono codici dei produttori (per esempio `not_found`, `bot_challenge`, `checkout_boundary`, `background_tab`, `timeout`, `navigation_error`, `out_of_stock`, `consent_blocking`, `empty_cart`; per il journey `page_unreadable`, `text_helper_unavailable`, `text_refused`, `cart_unreadable`, `final_page_unreadable`, `cart_items_unreadable`, `cart_price_unreadable`, `cart_price_ambiguous`, `journey_error`, `left_shop`) e il rapporto li scrive in italiano (`report.reason_label`; un test fallisce se un codice dei produttori non ha un'etichetta). `stage` è lo stadio del funnel (nel rapporto: fase): `not_requested` è "fase non richiesta" e `not_reached` "fase non raggiunta". Anche i codici con prefisso hanno un'etichetta: `not_applicable:`, `no_measurement:`, `limite superiore:`, `probe:`, `revisit_`, `guard_refused:`, `not_executed:`, `failed:`, `error:`, `credit_withheld:`; i rifiuti della guardia di sicurezza si compongono dietro `guard_refused:` e `probe:guard_refused:` (per esempio `probe:guard_refused:personal_field`: "test della ricerca non riuscito: rifiutato dalla guardia di sicurezza: campo per dati personali: mai compilato"). `personal_field:<token>`, `protected_field:<tipo>`, `forbidden_label:<chiave>` e `credit_withheld:<KPI>` mostrano il valore com'è scritto; un identificatore sconosciuto è mostrato come codice. I codici di rifiuto di `judgments.submit` non hanno etichetta del rapporto: servono all'host che giudica.
- **Confidenza A, B, C.** A = copertura almeno 85%, B = almeno 70%, C = almeno 60%. È un grado di copertura (la confidenza nel numero), non il livello del punteggio, e il rapporto non lo scrive come "Grado" isolato: nel riquadro principale, nelle righe dei sotto-indici e nel confronto per profilo compare come `Confidenza A (copertura 96 %)`. La percentuale accanto alla lettera non si arrotonda mai fino alla soglia del grado mancato: una copertura salvata di 0,8495 si legge `Confidenza B (copertura 84 %)`, con lo stesso valore nel riquadro, nel motivo e nella CLI. Un test del rapporto verifica "Confidenza" presente e "Grado " assente. Il riepilogo di `score_run` e `get_report` e l'output `score` e `report` della CLI usano la stessa dicitura. Nei dati il campo `grade` è la lettera nuda in ogni output JSON, comprese le righe di `list_runs`; l'unico punto del testo leggibile in cui la lettera compare da sola è la riga compatta di `jev-engage list` (`ERS 86,7 (A)`). Poiché i KPI non applicabili sono esclusi dalla copertura, un audit statico può leggere "Confidenza A" con quota LLM 0%: la lettera non dice quali famiglie mancano, lo dice la riga di ambito. Non si applica alcun tetto alla lettera in base al tipo di esecuzione, perché la renderebbe dipendente dal tipo di run e negherebbe per sempre la A alla modalità più riproducibile (l'audit deterministico da CLI). Opzione di fase 2, "copertura sul catalogo intero": se le famiglie non eseguite contassero contro la copertura, un audit statico con funnel completo leggerebbe 78% (B) e, con i giudizi, 88% (A).
- **Soglia di pubblicazione: 60%.** Sotto il 60% di copertura complessiva (`publish.min_coverage`) l'ERS non viene pubblicato. Regola aggiuntiva di `anchors.json`: ogni sotto-indice con peso almeno 15 (`publish.major_weight`: FAI, TRI, PTI, CCL, PERF; esente solo MPI) deve avere copertura almeno del 50% (`publish.min_major_coverage`); senza questa regola la media geometrica darebbe il peso pieno a un sotto-indice stimato su una frazione dei suoi KPI (vedi la tabella sotto). Quando non pubblica, il motivo è in italiano e dice quali coperture mancano, per esempio «copertura complessiva 12 % sotto il minimo del 60 %; copertura sotto il minimo del 50 % per Prestazioni (23 %), Attrito previsto (34 %), ...» (una copertura sotto il minimo non si arrotonda mai fino a raggiungerlo: «59,9 %»); lo stesso testo compare nel rapporto, nella CLI e in `score_run`.
- **Quota LLM** (`llm_share`): peso dei KPI giudicati / peso dei KPI valutati, per sotto-indice e complessiva. È riportata sempre (sezione 6.4).
- **Fallimento dell'agente.** La ricerca raccomanda di rieseguire con un altro seed o modello un journey fallito e di marcare l'esito "non confermato" se le esecuzioni discordano.
- **Intervalli e sensibilità.** La ricerca propone un intervallo ("ERS 71, 65-76, confidenza B") e una perturbazione Monte Carlo di pesi (±20%) e ancore (±5%) con stabilità dei ranghi, più un audit di correlazione su almeno 100 negozi (alfa di Cronbach per sotto-indice, PCA, fusione o riduzione di peso se |ρ| supera circa 0,8). Non sono in v1: sono fase 2 (sezione 8).

**Che cosa succede con un funnel incompleto.** `scoring.py` valuta un KPI dalle sue sole osservazioni valutate, e una riga valutata basta perché il KPI sia valutato: la copertura dipende quindi da ciò che `checks.py` emette per ogni stadio mancante. Regole per `checks.py` (vincolanti):

- **a. KPI di pagina.** Un'osservazione per ogni pagina distinta raggiunta tra gli stadi del KPI (qualunque stadio se il KPI non ha vincolo); con `repeats` maggiore di 1 una pagina e i suoi ricarichi (`repeat_of`) danno una riga, la mediana dei loro numeri. Per ogni stadio del KPI non raggiunto in un profilo, una riga con `assessed=False`, `page_id` nullo, lo stadio e il motivo che `run.not_assessable` registra per quello stadio (`not_found`, `bot_challenge`, `checkout_boundary`, `timeout`, `not_requested`; `not_reached` se non ne registra). Mai un valore per una pagina non raggiunta. Le pagine con stadio `extra` non sono pagine del funnel.
- **b. KPI di sito**: una riga per profilo, `page_id` nullo, dalle pagine raggiunte dei loro stadi (`evidence.stages_checked`). Uno stadio mancante non peggiora mai un valore: un KPI `any` con stadi propri (`PTI.SHIPPING_COST_PRE_CHECKOUT`, `PTI.VAT_STATED`, `TRI.JSONLD_*`) che vale falso mentre uno dei suoi stadi non ha dato una pagina utilizzabile non è valutato, con il motivo di quello stadio (`evidence.stages_missing`: anche uno stadio non richiesto conta come mancante); il vero da una pagina raggiunta vale. I KPI senza stadi (contatti, identificativo legale) leggono il chrome del sito su ogni pagina: il falso delle pagine raggiunte vale. Per i KPI di pagina con aggregazione `any` (`TRI.DELIVERY_TIME_STATED`, `FAI.BREADCRUMBS`) vale la regola g dello scoring.
- **c. I confronti hanno bisogno della loro base.** `PTI.FUNNEL_PRICE_DELTA` e `PTI.UNEXPLAINED_FEES` richiedono pagina prodotto e carrello; il checkout allunga solo la catena (un checkout che non si legge è saltato: `evidence.steps_skipped`). Con la sola pagina prodotto: non valutati, con il motivo dello stadio carrello. Un carrello vuoto (`crawler.cart_empty`: nessuna riga e nessun totale oltre zero, oppure la pagina lo dichiara) non è una base, e non lo è nemmeno per i KPI di carrello che dipendono dal contenuto (salienza della CTA, tempi di consegna, costi, spedizione mostrata, righe modificabili): non valutati, `empty_cart`. Un carrello con un totale ma senza righe leggibili non è vuoto: i controlli visti contano (`cart_lines_not_recognised` se non ce ne sono) e una CTA di checkout non riconosciuta è `checkout_cta_not_found`, mai una salienza 0.
- **d. Ospite e account.** `forms.guest_option`, `forms.login_required`, `forms.password_required` e `forms.password_present` sono booleani per pagina, valorizzati da `audit.js` su ogni tipo di pagina (carrello e primo step del checkout compresi); è `checks.py` a decidere quale pagina fa da evidenza, e consuma solo `guest_option` e `login_required`:
  - **Primo step del checkout raggiunto e leggibile** (anche attraverso il controllo ospite del carrello, 5.2): è l'unica evidenza. `FAI.GUEST_CHECKOUT` = `guest_option`, oppure `login_required` falso; `FAI.FORCED_ACCOUNT` è il suo contrario. I flag `password_required` e `password_present` restano in `evidence` e non decidono. Il secondo termine serve perché molti checkout chiedono email e indirizzo senza alcuna dicitura "ospite", e il solo lessico li valuterebbe 0.
  - **Il gate di accesso** (`login_required`, mai con un'uscita ospite sulla pagina o dentro il dialogo di accesso) è: (A) una password richiesta in un dialogo bloccante; (B) una password di registrazione obbligatoria fuori dagli overlay, anche accanto a un form di indirizzo (`new-password`, una seconda password nello stesso form, parole di registrazione), mai una password corrente; (C) una password o un testo di accesso obbligatorio in una pagina che non offre altro modo di inserire i propri dati. Un box "Sei già cliente? Accedi" con password corrente accanto a un form di spedizione non è un gate, né lo sono un invito ad accedere per fare prima o un "Hai già un account?".
  - **Carrello.** Solo se il checkout non ha dato evidenza (non raggiunto, o raggiunto ma illeggibile) e il carrello ha `guest_option` vero: `FAI.GUEST_CHECKOUT` vero e `FAI.FORCED_ACCOUNT` falso, sulla pagina del carrello, stadio `cart`. La riga porta sempre `evidence.checkout_entry` (`page_id` e `reason`: la pagina di checkout illeggibile, oppure `page_id` nullo e il motivo dello stadio). Se `guest_option` è falso il carrello non emette nulla: l'assenza di una menzione dell'ospite nel carrello non è evidenza. Il crawler entra nel checkout dal controllo ospite del carrello quando esiste (5.2), quindi la riga del carrello è il ripiego.
  - **Né l'uno né l'altro.** Se il carrello non ha un controllo ospite e il checkout non è stato raggiunto, i due KPI sono non valutati, con il motivo che `run.not_assessable` registra per lo stadio del checkout (e per il carrello se non è stato raggiunto).
  - **Carrello e checkout discordi.** Prevale il checkout: la riga del carrello è usata solo quando il checkout non ha dato evidenza. Le aggregazioni `any` di `kpis.py` mantengono così `FAI.GUEST_CHECKOUT` e `FAI.FORCED_ACCOUNT` complementari: mai entrambi veri.
- **e. Non applicabile** (`not_applicable:*`) solo per ciò che esiste e non si applica, mai per uno stadio non raggiunto: nessuna variante, nessun prezzo barrato, nessun campo di form, un prodotto che non si può comprare (`not_applicable:out_of_stock` per `FAI.PDP_CTA_ABOVE_FOLD` e la salienza in PDP), i tempi di carico di un documento che non è una navigazione a freddo (`not_applicable:speculative_navigation` per quello che le regole di speculazione del negozio hanno caricato prima del click, `not_applicable:same_document` per il documento di un record precedente). Un'aggiunta al carrello disabilitata solo finché non si sceglie una variante vale come abilitata per `FAI.PDP_CTA_ABOVE_FOLD` (`evidence.disabled_until_variant`): il KPI riguarda il raggiungerla senza scorrere.
- **f. Prezzi barrati e dicitura Omnibus.** `PTI.STRIKETHROUGH_LOWEST30` confronta i prezzi barrati e le diciture "prezzo più basso degli ultimi 30 giorni" dentro e fuori le schede prodotto solo se `audit.js` marca entrambi (`in_card`); senza marcatori basta una dicitura in pagina (`evidence.statement_scope: unknown`), perché la dicitura di una scheda e una nota a piè di pagina non si distinguono.
- **g. Regola `any` dello scoring** (`scoring._complete_any`). Per ogni gruppo di profilo (le sue righe più quelle senza profilo), un KPI `any` senza righe vere e con una riga non valutata il cui motivo non è `not_applicable*` è escluso da quel profilo; con tutti i profili esclusi il KPI non è valutato, con il primo di quei motivi. Un KPI come `TRI.DELIVERY_TIME_STATED` con la pagina prodotto falsa e il carrello `not_requested` non è valutato («fase non richiesta») e non diventa mai un falso valutato.

Calcolo dal registro e da `anchors.json` v1 per un audit deterministico (senza journey né giudizi; pesi applicabili: PERF 15,0, FAI 22,5, TRI 16,5, PTI 13,5, CCL 8,5, MPI 3,5), rifatto il 2026-10-07 con `checks.observations` e `scoring.score_run` su una run reale dell'audit della fixture locale (due profili, funnel completo) da cui si tolgono le pagine degli stadi non raggiunti, registrandoli come non valutabili. Copertura dei sotto-indici sotto il 100% e copertura complessiva (PERF e MPI restano al 100% in ogni riga, quindi non compaiono):

| Stadi non raggiunti | Sotto-indici sotto il 100% | Complessiva | ERS |
|---|---|---|---|
| `checkout_entry` | FAI 73% (87% solo se il carrello espone un controllo per l'ospite) | 94% (97% con quel controllo) | pubblicato |
| carrello e `checkout_entry` | FAI 69%, PTI 70% | 89% | pubblicato |
| pagina prodotto, carrello e `checkout_entry` | FAI 56%, TRI 61%, PTI 19%, CCL 76% | 67% | non pubblicato (PTI sotto il 50%) |

Un checkout non raggiunto non blocca quindi mai la pubblicazione: la blocca solo un funnel che si ferma prima della pagina prodotto, e giustamente, perché senza pagina prodotto non esiste una stima di readiness. La tabella vale finché `checks.py` e `anchors.json` non cambiano: va ricalcolata con lo stesso procedimento quando cambiano (PTI 19% è 2,5 su 13,5 di peso, cioè 18,5%).

### 3.6 Una sola casa per segnale

Regole di proprietà (ricerca, sezione 2.4), valide per ogni nuovo KPI.

| Segnale | Casa | Eccezione |
|---|---|---|
| Registrazione obbligatoria | FAI (`FAI.FORCED_ACCOUNT`) | In DPR solo se nascosta o ingannevole |
| Costi nascosti o non spiegati | PTI (`PTI.FUNNEL_PRICE_DELTA`, `PTI.UNEXPLAINED_FEES`) | In DPR solo il meccanismo ingannevole (sneak into basket) |
| Errori JavaScript e HTTP | PERF | Non contano in FAI né in TRI |
| Contrasto della CTA | CCL (`CCL.CTA_SALIENCE`) | Non in FAI |
| Etichette dei campi (regole axe su etichette e nomi) | CCL (`CCL.FORM_LABELS`), non FAI: la ricerca le assegnava a FAI, ma la sua regola vera è "una sola casa" e il piano approvato elenca "label dei form" in CCL e "a11y base" in FAI | `FAI.A11Y_BASIC` non le conta (4.8) |
| Timer di urgenza | MPI solo se supera il test di inganno | Se lo fallisce, va in DPR e il credito MPI è ritirato |
| Scorte basse | MPI solo se coerenti | Se incoerenti, DPR |

Le sovrapposizioni che il registro v1 presenta sono discusse in 4.8, con la regola di conteggio fissata.

### 3.7 Sistemi di scoring esistenti (confronto e idee riprese)

| Sistema | Come valuta | Che cosa riprendiamo | Stato |
|---|---|---|---|
| Lighthouse, performance | Pesi FCP 10, Speed Index 10, LCP 25, TBT 30, CLS 25; INP presente con peso 0; punteggi di metrica da curve log-normali su dati HTTP Archive; fasce 0-49, 50-89, 90-100; il 25° percentile vale 50 e l'8° vale 90 | Normalizzazione con punti di controllo e pesi pubblici fissi | [V] default-config.js, docs Chrome |
| Lighthouse, accessibilità | Pesi per impatto axe: critical 10, serious 7, moderate 3, minor 1; include `target-size` (7) | Punteggio pesato per severità (fase 2 con axe-core completo) | [V] stesso file |
| Baymard UX benchmark | Linee guida valutate su scala a 7 livelli; importanza = severità x frequenza; N/A escluso dal denominatore; "normalizzazione autocorrettiva" periodica; cinque livelli da poor a perfect | Pesi severità x frequenza; N/A fuori dal denominatore | [V]. Il numero di linee guida è incoerente tra le pagine (1.254 contro 810) |
| PURE | Esperti valutano ogni passo da 1 a 3; punteggio compito = somma; il passo rosso colora tutto il compito; r circa 0,5 con SEQ e SUS, circa 0,6 con SUPR-Q, nessuna correlazione significativa con tempo e completamento | Punteggio per passo con peggior caso; **tetto realistico di validità** (sezione 7) | [V] |
| Nielsen, 10 euristiche | Severità 0-4 che combina frequenza, impatto, persistenza | Checklist per l'LLM, ma con severità ridotta a presenza o assenza | [V] |
| SUPR-Q | 8 item su usabilità, credibilità e fiducia, aspetto, fedeltà; percentili su un database di 200 siti | Struttura delle dimensioni; percentile sul corpus (fase 2) | [V]; dimensione del database [S] |
| Microsoft Clarity, Contentsquare | Rage click, dead click, scroll eccessivo, quick back, errori. Contentsquare: frustration score 1-100 con algoritmo non pubblicato | Proxy osservabili di frustrazione; bersagli di calibrazione | Clarity [V]; Contentsquare [S] e vendor |
| GA4 | Sessione con engagement: oltre 10 s, oppure almeno un evento chiave, oppure almeno 2 pagine o schermate. Tasso di engagement = sessioni con engagement / sessioni; bounce rate = 1 − tasso di engagement | Bersaglio di calibrazione | [V] |

---

## 4. Catalogo dei KPI

Gli 89 KPI di `jev_ultrafast/engagement/kpis.py`, raggruppati per indice: PERF 16, FAI 29, TRI 13, PTI 8, CCL 9, MPI 5, DPR 9. Ogni riga ha id, definizione, metodo di misura, unità, ancore v2 con fonte, stadi del funnel e forza dell'evidenza (il suffisso (cat.) segna i gradi assegnati dal catalogo, vedi la legenda iniziale). I DPR hanno una tabella propria con severità e test di inganno.

**Metodo di misura**

| Codice | Significato |
|---|---|
| CDP | Deterministico, da eventi CDP (rete, runtime, log) o da API di prestazioni nella pagina iniettate da `vitals.js` (PerformanceObserver, Navigation Timing) |
| DOM | Deterministico, da una sola lettura dell'intero documento con `audit.js`: DOM, stili calcolati, JSON-LD, geometria, lessico IT/EN |
| IMG | Deterministico, dallo screenshot, con Pillow |
| JNY | Dal journey: `steps.jsonl`, `vitals.js` (`mark()`, `since()`), oracolo indipendente |
| LLM | Giudizio LLM su snippet estratti, con etichetta chiusa (sezione 6); la riga dice se lo dà Jev (un campione, con soglia e controllo a ordine invertito) o Claude (tre campioni) |
| DEC | Test di inganno in contesti isolati freschi dello stesso profilo: visite ripetute dove il segnale varia (timer, scorte, overlay), confronto a visita singola per gli altri (sezione 2.6) |

**Stadi.** `home`, `plp`, `pdp`, `cart`, `checkout` (= `checkout_entry`, il primo step del checkout; mai oltre). "tutti" indica un KPI senza vincolo di stadio. "journey" indica un KPI che esiste solo se è stato eseguito un journey.

**Ancore.** I punti sono nella notazione di `anchors.json` (`valore→punteggio`, punto decimale, nessun separatore delle migliaia). Il punteggio di un KPI è l'interpolazione lineare tra i punti, tagliata agli estremi. Pesi dei KPI dentro il sotto-indice: solo in `anchors.json`.

**Above the fold.** Un elemento è above the fold quando il centro del suo riquadro cade nella prima schermata, in entrambi gli assi, a scorrimento zero (in coordinate di pagina: `0 ≤ x + w/2 <` larghezza e `0 ≤ y + h/2 <` altezza del viewport), il criterio con cui `snapshot.js` e `browser.py` trattano un controllo come cliccabile senza scorrere. Una CTA di cui si vedono solo i primi pixel in fondo allo schermo è sotto la piega, e ciò che sta in un pannello spostato fuori dallo schermo (`translateX(±100%)`) non è mai above the fold. Vale per ogni campo `above_fold` di `audit.js`: CTA, prezzi, campo di ricerca, schede prodotto.

### 4.1 PERF: performance e robustezza (16 KPI, peso 18)

Tutti i KPI sono per pagina e per profilo (mobile e desktop), tranne quelli marcati "journey". Per i KPI con aggregazione `wmedian`: mediana pesata con peso 2 per PDP e PLP (`stage_weights`). Le bande sono pubblicate per TTFB, FCP, LCP e CLS (web.dev) e per i limiti di risposta NN/g; le altre sono editoriali, comprese quelle che trasferiscono una soglia pubblicata a una grandezza diversa (`PERF.TBT_APPROX`, `PERF.INP_SYNTH`, `PERF.CLS_POST_INPUT`). Le soglie CWV non sono cambiate: alcuni blog SEO parlano di "punteggi compositi" del 2026, che non è stato possibile confermare su web.dev e che qui si considerano non verificati.

**Nota di metodo sui tempi di pagina.** Ogni tempo è una navigazione a freddo per URL (`Page.navigate`, cache disabilitata, contesto nuovo) e la prerenderizzazione è disabilitata. Un documento che le regole di speculazione del negozio hanno caricato prima del click (prefetch o prerender) non è una navigazione a freddo: i suoi tempi sono `not_applicable:speculative_navigation` (i byte restano contati nel record che lo consuma, e un prefetch mai usato non è contato da nessuno: la pagina che lo dichiara ha la nota `speculation_rules`). Il documento di un record precedente (cambio di route lato client, ripristino da back-forward cache) è `not_applicable:same_document`. Un documento che non si è caricato (la pagina di errore di Chrome) non ha tempi né audit: `navigation_error`. FCP, LCP, CLS, TBT e LOAF di una pagina nascosta al caricamento sono `background_tab`.

| ID | Definizione | Metodo | Unità | Ancore v2 e fonte | Stadi | Evidenza |
|---|---|---|---|---|---|---|
| `PERF.TTFB` | Tempo dall'inizio della navigazione al primo byte della risposta finale; include redirect, DNS, TLS e, sul profilo mobile, la latenza emulata | CDP: `receiveHeadersEnd` della risposta finale del Document principale, misurato dalla sua prima `requestWillBeSent` (non `navigation.responseStart`). Sul profilo mobile, a rete emulata, Navigation Timing non vede la latenza emulata e non fa da ripiego: senza l'evento CDP il KPI è `unavailable`. Sul desktop il ripiego è Navigation Timing (`ttfb_source: navigation_timing`) | ms | Pubbl. `800→100` `1800→50` `3600→0`. web.dev TTFB: buono ≤0,8 s, scarso >1,8 s ("guida approssimativa"); lo 0 a 2x la soglia "scarso" è editoriale | tutti | Moderata (cat.; guida approssimativa) |
| `PERF.FCP` | Primo testo, immagine, SVG o canvas non bianco dipinto | CDP: entry `paint` | ms | Pubbl. `1800→100` `3000→50` `6000→0`. web.dev FCP: buono ≤1,8 s, scarso >3,0 s; 0 editoriale | tutti | Forte (cat.; soglia) |
| `PERF.LCP` | Rendering dell'immagine, blocco di testo o video più grande nel viewport, dall'inizio della navigazione. L'elemento è registrato come descrittore, mai come selettore | CDP: `largest-contentful-paint` | ms | Pubbl. `2500→100` `4000→50` `8000→0`. Core Web Vitals, 75° percentile: buono ≤2,5 s, scarso >4,0 s; 0 editoriale | tutti | Forte (cat.; soglia) |
| `PERF.CLS` | Finestra di sessione più grande della somma impatto x distanza (pause sotto 1 s, massimo 5 s); esclude gli shift con `hadRecentInput` | CDP: `layout-shift` | punteggio | Pubbl. `0.1→100` `0.25→50` `0.5→0`. Core Web Vitals: buono ≤0,1, scarso >0,25; 0 editoriale | tutti | Forte (cat.; soglia) |
| `PERF.CLS_POST_INPUT` | Somma degli shift con `hadRecentInput=true` dopo le azioni dell'agente. Il CLS standard esclude gli shift entro 500 ms da un input discreto, quindi serve una misura a parte; si somma su tutte le interazioni misurate del journey | JNY: `since()` | punteggio | Provv. `0.1→100` `0.25→50` `0.5→0`: stesse bande del CLS, perché per questi shift non esiste una soglia pubblicata | journey | Debole (cat.) |
| `PERF.TBT_APPROX` | Somma di (durata − 50 ms) dei long task tra FCP e load + 3 s. Il TTI è deprecato, quindi è un'approssimazione, etichettata "TBT-approx"; proxy di laboratorio dell'INP | CDP: `longtask` | ms | Provv. `200→100` `600→50` `1500→0`. web.dev TBT: obiettivo <200 ms su mobile. Le bande 200/600 ms di Lighthouse sono riportate da memoria nella ricerca, non riverificate, e la finestra misurata (da FCP a load + 3 s) non è quella del TBT di Lighthouse (da FCP a TTI): le soglie pubblicate non si trasferiscono alla grandezza approssimata | tutti | Moderata (cat.; proxy: costrutto TBT [V] web.dev, finestra approssimata) |
| `PERF.LOAF_COUNT` | Numero di long animation frame (oltre 50 ms), con attribuzione per script. Solo Chromium 123 o successivo | CDP: `long-animation-frame` | conteggio | Provv. `0→100` `3→60` `10→0`. Nessuna soglia ufficiale | tutti | Debole (cat.) |
| `PERF.INP_SYNTH` | Durata massima Event Timing dei click e delle digitazioni dell'agente nella stessa pagina (non delle selezioni, né dei click che aprono un nuovo documento), mai sotto i 16 ms della soglia di Event Timing: se nessuna interazione la raggiunge il valore è 16, con motivo «limite superiore». Dichiarata "latenza di interazione dei click dell'agente": non è confrontabile con l'INP reale (CrUX). È ottimistica, perché le azioni sono distanziate da secondi di latenza LLM e il main thread è quasi sempre libero | JNY: Event Timing | ms | Provv. `200→100` `500→50` `1000→0`. Bande INP (buono ≤200 ms, scarso >500 ms) applicate ai click dell'agente: la grandezza è diversa da quella per cui sono definite e non è confrontabile con CrUX, quindi l'ancora è editoriale come per `PERF.CLS_POST_INPUT`. Ipotesi della ricerca, da verificare sullo stack: le azioni inviate via CDP dovrebbero produrre eventi trusted con voci Event Timing | journey | Debole (cat.; proxy) |
| `PERF.BYTES_TOTAL` | Byte trasferiti (codificati, `encodedDataLength` di CDP). Si preferiscono i byte CDP a `transferSize` di Resource Timing, che per le risorse cross-origin senza Timing-Allow-Origin vale 0 (comportamento generale dei browser, non riverificato dalla ricerca) | CDP: rete | KB | Provv. `1600→100` `2500→60` `5000→20` `8000→0`. Lighthouse: obiettivo <1.600 KiB, segnala >5.000 KiB (circa 1.640 e 5.120 KB, arrotondati a 1600 e 5000 KB in `anchors.json`); mediana della home nel Web Almanac 2025: 2.862 KB desktop, 2.559 KB mobile. Interpolazione e 8000 editoriali; in `anchors.json` 1 KB = 1000 byte, Lighthouse usa i KiB | tutti | Moderata (cat.; dati descrittivi) |
| `PERF.BYTES_JS` | Byte JavaScript trasferiti (`resourceType` script) | CDP: rete | KB | Provv. `300→100` `650→60` `1500→20` `3000→0`. Mediana Web Almanac 2025: 697 KB desktop, 632 KB mobile; gli altri punti sono editoriali | tutti | Moderata (cat.; dati descrittivi) |
| `PERF.REQUESTS` | Numero di richieste (ogni salto di redirect conta; esclusi gli URL `data:`, `blob:` e `about:`) | CDP: rete | conteggio | Provv. `50→100` `75→60` `150→20` `250→0`. Mediana Web Almanac 2025: 77 desktop, 72 mobile; gli altri punti sono editoriali | tutti | Debole (cat.) |
| `PERF.THIRD_PARTY_SHARE` | Quota dei byte provenienti da domini di terze parti (confronto tra domini registrabili, con tabella dei ccTLD di secondo livello: è un'approssimazione) | CDP: rete | % | Provv. `20→100` `50→50` `80→0`, editoriali. Contesto: il 92% delle pagine usa almeno una terza parte; gli script sono il 30,5% delle richieste di terze parti (Web Almanac 2024) | tutti | Debole (cat.) |
| `PERF.IMG_OVERSIZED` | Immagini visibili e già caricate (esclusi gli SVG) con larghezza naturale almeno 2x quella visualizzata moltiplicata per il DPR del profilo | DOM | conteggio | Provv. `0→100` `3→60` `10→0`, editoriali. Contesto: Google 2017, il 30% delle pagine mobile poteva risparmiare oltre 250 KB con la compressione | tutti | Debole (cat.) |
| `PERF.CONSOLE_ERRORS` | Errori `console.error` e `console.assert`, eccezioni non gestite (Chrome riporta come eccezioni anche le promise rifiutate non gestite) e voci di log di livello errore, escluse quelle di rete, che sono in `PERF.HTTP_ERRORS`; contano anche gli errori degli iframe cross-site. Somma sul funnel per profilo; il valore complessivo è quello del profilo peggiore | CDP: runtime e log | conteggio | Provv. `0→100` `3→60` `15→0`, editoriali. Baymard: il 17% abbandona per errori o crash del sito (motivo dichiarato dall'utente, che qui è approssimato da un conteggio) | tutti | Moderata (cat.; costrutto), debole (cat.; proxy) |
| `PERF.HTTP_ERRORS` | Risposte HTTP 4xx e 5xx (escluso il favicon cercato dal browser); le richieste fallite senza risposta non contano. Somma sul funnel per profilo | CDP: rete | conteggio | Provv. `0→100` `2→60` `10→0`, editoriali. Stessa fonte Baymard | tutti | Moderata (cat.; costrutto), debole (cat.; proxy) |
| `PERF.ACTION_RESPONSE_MS` | Mediana del tempo dall'azione dell'agente alla prima risposta visibile: la prima mutazione del DOM o navigazione. Una richiesta da sola non è una risposta (un beacon di tracciamento compreso). Contano le interazioni nella stessa pagina che hanno ricevuto una risposta; quelle che aprono un nuovo documento sono misurate da FCP e LCP, e quelle senza risposta sono evidenza di `FAI.DEAD_CLICK_RATE`. Il cronometro parte dopo il controllo di freschezza dell'harness (una lettura di `snapshot.js`), subito prima dell'input: per un TYPE_TEXT comprende l'invio dell'input (click sul campo, selezione, inserimento del testo) | JNY: `since()` | ms | Pubbl. `100→100` `1000→60` `3000→20` `10000→0`. Limiti NN/g: 0,1 s sembra istantaneo, 1 s mantiene il flusso di pensiero, 10 s perde l'attenzione; il punto a 3000 è editoriale | journey | Moderata (cat.) |

**Evidenza pubblicata su velocità, conversione ed engagement.** Salvo dove indicato sono pubblicazioni di aziende o committenti Google, e per lo più osservazionali.

| Studio | Risultato | Disegno | Cautele |
|---|---|---|---|
| Deloitte Digital e 55 per Google, "Milliseconds Make Millions" (2020), vendor [S] | 0,1 s in meno: conversione retail +8,4%, spesa +9,2%; PLP→PDP +3,2%, PDP→carrello +9,1%, carrello→checkout +3,9%, checkout→ordine +4,7%; PDP→carrello nel lusso +40,1% | 37 siti, oltre 30 milioni di sessioni, 30 giorni a fine 2019 | I 0,1 s sono applicati insieme a quattro metriche (First Meaningful Paint, Estimated Input Latency, caricamento osservato, latenza massima del server); due sono ormai deprecate |
| Google e SOASTA (2017), vendor [V] | Da 1 s a 7 s di caricamento la probabilità di rimbalzo mobile cresce del 113%; da 400 a 6.000 elementi la probabilità di conversione cala del 95%; la pagina mobile media impiegava 22 s su 3G; accuratezza della rete neurale 90% | 900.000 landing page, 3G emulato | Gli altri intervalli citati spesso (+32% da 1 a 3 s, +90% da 1 a 5 s, +106% da 1 a 6 s, +123% da 1 a 10 s) vengono solo da sintesi secondarie: **non verificati** |
| Google e DoubleClick (2016), vendor | Il 53% delle visite mobile viene abbandonato se la pagina impiega oltre 3 s | Google Analytics, 3.700 siti | Nota a piè di pagina dello stesso PDF |
| Akamai, Online Retail Performance (primavera 2017), vendor | 100 ms di ritardo riducono la conversione del 7%; 2 s di ritardo alzano il rimbalzo del 103%; conversione massima 12,8% su desktop a 1,8 s e 3,3% su mobile a 2,7 s | Circa 10 miliardi di visite in un mese | Osservazionale |
| Portent (2022), vendor | Conversione B2C e-commerce 3,05% a 1 s, 1,68% a 2 s, 1,12% a 3 s, 0,67% a 4 s | 6 siti e-commerce e 14 B2B, oltre 100 milioni di pagine viste | Pochi siti |
| Vodafone (2021) | LCP migliorato del 31% (da 8,3 a 5,7 s); vendite +8%, lead-to-visit +15%, cart-to-visit +11% | A/B 50/50 di una sola landing page, traffico a pagamento | Più solido di una correlazione, ma una sola pagina; pagina funzionalmente identica |
| Rakuten 24 | Ricavo per visitatore +53,37%, conversione +33,13%, valore medio d'ordine +15,20%, exit rate −35,12% | A/B 50/50 di un mese, caricamento mobile più veloce di 0,4 s | Una sola pagina |
| Farfetch | Conversione −1,3% ogni +100 ms di LCP; l'exit rate migliora del 3,1% per ogni −0,01 di CLS; conversione +2,8% ogni −1 s di TTI | Correlazioni RUM con web-vitals, più A/B con +1-5% di conversione | Correlazionale |
| redBus | INP −72% sulla pagina di ricerca, vendite +7% | RUM, 95° percentile | Correlazionale |

Cautele generali: gli studi RUM correlazionali (Farfetch, Portent, Akamai, SOASTA) non isolano la causalità; i due A/B (Vodafone, Rakuten) sono più solidi ma riguardano una pagina ciascuno; tutti sono promossi dalle aziende coinvolte. "+X% ogni 100 ms" è indicativo, non un'elasticità trasferibile. Le misure di laboratorio sono a cache fredda, da una sola località e da un solo dispositivo, senza personalizzazione né interazione reale; i dati di campo (CrUX) restano il riferimento per le priorità e sono in fase 2.

### 4.2 FAI: frizione e abilità (29 KPI, peso 22)

#### 4.2.1 Ricerca e navigazione (8 KPI)

| ID | Definizione | Metodo | Unità | Ancore v2 e fonte | Stadi | Evidenza |
|---|---|---|---|---|---|---|
| `FAI.SEARCH_VISIBLE` | Campo di ricerca visibile above the fold. Un campo di testo (input senza tipo, `text` o `search`, `role=searchbox` o `combobox`) è di ricerca se somma almeno 2 punti: `type=search` vale 3; `role=searchbox`, contenitore `role=search` (o `<search>`) e nome `q`, `s`, `query`, `search` e simili valgono 2 ciascuno, come l'etichetta o il placeholder con una parola del lessico IT/EN (insieme, un solo indizio da 2); azione del form e nome o id con una parola di ricerca valgono 1 ciascuno. Un solo indizio forte basta. Un'icona che apre la ricerca senza un campo visibile non conta, né un campo dentro un menu laterale chiuso (un pannello spostato fuori dallo schermo, `translateX(±100%)`, che resta fuori anche dalle CTA e dai bersagli touch). Above the fold: il centro del campo nella prima schermata, in entrambi gli assi, a scorrimento zero (vedi l'apertura della sezione 4) | DOM | bool | Pubbl. `true→100` `false→0`. NN/g: campo di testo visibile in alto, non un link; passare da link a campo visibile ha alzato l'uso della ricerca del 91% | home | Moderata (cat.) |
| `FAI.SEARCH_WIDTH` | Larghezza in px del campo di ricerca visibile (massimo tra le pagine in cui esiste) | DOM | px | Provv. `100→0` `200→60` `300→100`. NN/g: abbastanza largo per una query tipica; i px sono editoriali | home | Debole (cat.) |
| `FAI.SEARCH_AUTOCOMPLETE` | Suggerimenti di ricerca, misurati solo con una sonda del crawler: si digita nel campo osservato una parola presa da un'etichetta di categoria osservata e si attendono al più 800 ms dalla fine della digitazione per voci `role=option` visibili (massimo tra le pagine in cui la sonda è stata eseguita). La guardia decide prima di digitare: mai dati personali. Non valutabile: `probe_not_run`, `probe:<motivo>` (per esempio `probe:guard_refused:personal_field`); senza campo di ricerca `not_applicable:no_search_field` | DOM, sonda | bool | Pubbl. `true→100` `false→0`. Baymard, benchmark sulla ricerca (anno non indicato nella ricerca): l'80% dei siti offre autocomplete (il 19% al livello alto) | home | Moderata (cat.) |
| `FAI.PLP_FILTERS` | Numero di gruppi di filtro nella categoria (`fieldset`, `details`, `role=group` o intestazioni nella regione dei filtri: un `aside` o un blocco con almeno 3 scelte), oppure 1 se esiste solo un pulsante «Filtri» | DOM | conteggio | Provv. `0→0` `3→60` `5→100`. Baymard (anno non indicato nella ricerca): il 57% dei siti non offre tutti e 5 i tipi di filtro essenziali; il conteggio dei controlli è un proxy | plp | Moderata (cat.) |
| `FAI.PLP_SORT` | Ordinamento presente: un `select` con almeno 2 opzioni di ordinamento del lessico IT/EN (prezzo, valutazione, novità, più venduti) o un pulsante di ordinamento | DOM | bool | Pubbl. `true→100` `false→0`. Baymard (anno non indicato nella ricerca): il 64% dei siti non offre tutti e 4 gli ordinamenti essenziali (prezzo, valutazione, più venduti, novità) | plp | Moderata (cat.) |
| `FAI.PLP_RESULT_COUNT` | Numero di risultati mostrato nel listing | DOM | bool | Provv. `true→100` `false→0`. Convenzione editoriale: orienta l'utente nel listing | plp | Debole (cat.) |
| `FAI.PLP_PAGINATION` | Tipo di paginazione: `load_more` (pulsante «carica altri»), `pagination` (`rel=next`, nav di paginazione, link numerati), `none`. `infinite` è previsto da `checks.py` e dalle ancore, ma `audit.js` non lo rileva: un listing a scorrimento infinito oggi vale `none` | DOM | enum | Provv. `load_more→100` `pagination→80` `infinite→60` `none→50`. Punteggi editoriali: la preferenza per load-more che `anchors.json` attribuisce a Baymard non è verificata nei file di ricerca | plp | Debole (cat.) |
| `FAI.BREADCRUMBS` | Breadcrumb su categoria e prodotto (`nav[aria-label*=breadcrumb]` o JSON-LD `BreadcrumbList`) | DOM | bool | Pubbl. `true→100` `false→0`. NN/g: adatti alle gerarchie profonde e all'e-commerce; mostrano la gerarchia, non la cronologia | plp, pdp | Debole (cat.) |

#### 4.2.2 Pagina prodotto e carrello (3 KPI)

| ID | Definizione | Metodo | Unità | Ancore v2 e fonte | Stadi | Evidenza |
|---|---|---|---|---|---|---|
| `FAI.PDP_CTA_ABOVE_FOLD` | CTA di aggiunta al carrello visibile, abilitata e con il centro nella prima schermata a scorrimento zero (vedi l'apertura della sezione 4: una CTA di cui si vedono solo i primi pixel in fondo allo schermo non conta); riconosciuta dal lessico IT/EN, mai da selettori propri del sito. Aggregazione `all`: deve valere su tutte le PDP. Un'aggiunta disabilitata solo finché non si sceglie una variante vale come raggiungibile (`evidence.disabled_until_variant`); un prodotto esaurito è `not_applicable:out_of_stock` | DOM | bool | Pubbl. `true→100` `false→0`. NN/g: il 57% del tempo di visione cade above the fold e il 74% nelle prime due schermate (120 partecipanti, 130 mila fissazioni; cifre da sintesi di ricerca [S]) | pdp | Debole o moderata (gruppo Prompt della ricerca) |
| `FAI.PDP_VARIANT_SELECTOR` | Tipo di selettore delle varianti: `buttons` se almeno un gruppo di varianti è fatto di pulsanti, `select` se esistono solo altri controlli, `none`. `none` indica un prodotto senza varianti ed è "non applicabile", non 0 | DOM | enum | Provv. `buttons→100` `select→50` `none→non applicabile`. Baymard (anno non indicato nella ricerca): nel 57% dei siti la scelta della taglia non avviene con pulsanti | pdp | Moderata (cat.) |
| `FAI.CART_EDITABLE` | Quantità o rimozione modificabili nel carrello: almeno una riga con campo quantità o controllo di rimozione. Carrello vuoto: non valutato (`empty_cart`); righe che `audit.js` non riconosce e nessun controllo visto: `cart_lines_not_recognised` | DOM | bool | Provv. `true→100` `false→0`. La ricerca elenca righe, subtotale, stime di spedizione e tasse, CTA di checkout e opzione ospite come elementi attesi nel carrello; nessuna linea guida pubblicata verificata (`anchors.json` la dichiara editoriale) | cart | Debole (cat.) |

#### 4.2.3 Checkout, primo step (4 KPI)

| ID | Definizione | Metodo | Unità | Ancore v2 e fonte | Stadi | Evidenza |
|---|---|---|---|---|---|---|
| `FAI.CHECKOUT_FIELDS` | Campi visibili e abilitati al primo step (`input`, `select`, `textarea`; esclusi hidden, submit, button, reset, image, i campi di ricerca e quelli di overlay, testata, navigazione e piè di pagina; i radio dello stesso nome contano una volta). Se il checkout è raggiunto dal controllo ospite del carrello, si conta lo step che quel controllo apre. Regola di conteggio unica: Baymard usa anche i "form element" (23,48 tipici contro 12-14 ottimali), non riconciliati con i "form field" | DOM | conteggio | Provv. `8→100` `11.3→60` `16→20` `20→0`: soglia pubblicata trasferita a una grandezza diversa (legenda), da ricalibrare sui conteggi del primo step. Baymard 2024: media 11,3 campi (11,8 nel 2021, 12,7 nel 2019), "la maggior parte dei siti ne ha bisogno di 8"; il numero di campi pesa sull'usabilità molto più del numero di passi. Baymard conta i campi sull'intero checkout e qui si contano quelli del solo primo step: l'ancora è indulgente per i checkout a più passi (lo dichiara `anchors.json`). I punti 16→20 e 20→0 sono editoriali; il prior della ricerca era ≤8 = 100, 11-12 = 60, ≥20 = 0 | checkout | Forte |
| `FAI.GUEST_CHECKOUT` | Acquisto come ospite disponibile. Evidenza: il primo step del checkout, se raggiunto e leggibile (3.5, regola d): `forms.guest_option` (controllo o etichetta del lessico `guest` visibile, anche dentro il dialogo di accesso: guest, ospite, gast, "continua senza account"), oppure `forms.login_required` falso. La seconda condizione serve perché molti checkout chiedono email e indirizzo senza dicitura "ospite" e il solo lessico li valuterebbe 0. Il gate di accesso (`login_required`, mai con un'uscita ospite) è: (A) una password richiesta in un dialogo bloccante; (B) una password di registrazione obbligatoria fuori dagli overlay, anche accanto a un form di indirizzo (`new-password`, una seconda password nello stesso form, parole di registrazione), mai una password corrente; (C) una password o un testo di accesso obbligatorio in una pagina che non offre altro modo di inserire i propri dati. Un box "Sei già cliente? Accedi" con password corrente accanto a un form di spedizione non è un gate, né lo è l'invito ad accedere per fare prima. Il carrello conta solo se il checkout non ha dato evidenza: un controllo ospite osservato nel carrello (da cui il crawler entra nel checkout, 5.2). Senza checkout né controllo ospite nel carrello: non valutato | DOM | bool | Pubbl. `true→100` `false→0`. Baymard: il 18% abbandona se il sito vuole la creazione di un account; il 47% dei siti non rende l'ospite l'opzione più evidente (benchmark 2025, oltre 180 siti; un dato Baymard più vecchio era 62%) | cart, checkout | Forte |
| `FAI.FORCED_ACCOUNT` | Registrazione obbligatoria prima del pagamento: password di registrazione obbligatoria o gate di accesso senza altra via d'ingresso, nessuna alternativa ospite. KPI negativo. Al primo step del checkout è il contrario di `FAI.GUEST_CHECKOUT` (definizione del gate in quella riga); dal carrello vale falso solo se il carrello espone un controllo ospite e il checkout non ha dato evidenza (3.5, regola d). Una riga decisa dal carrello senza una pagina di checkout leggibile riporta in `evidence.checkout_entry` quella pagina (se c'è) e il motivo | DOM | bool | Pubbl. `true→0` `false→100`. Baymard: 18% abbandona per la creazione dell'account; il 42% dei siti non rinvia la creazione dell'account a dopo l'ordine | cart, checkout | Forte |
| `FAI.AUTOCOMPLETE_ATTRS` | Quota dei campi visibili con attributo `autocomplete` valido (token `email`, `given-name`, `street-address`...; checkbox, radio, range, color e file non entrano nel denominatore). Si legge l'attributo, non si compila nulla. Aggregazione `min` sulle pagine | DOM | % | Provv. `0→0` `50→50` `100→100`. WCAG 1.3.5 (scopo dell'input), citato nella ricerca; la linearità è editoriale | checkout | Moderata (cat.; norma; effetto sulla conversione non provato) |

#### 4.2.4 Target, accessibilità di base, overlay (5 KPI)

| ID | Definizione | Metodo | Unità | Ancore v2 e fonte | Stadi | Evidenza |
|---|---|---|---|---|---|---|
| `FAI.TARGET_SIZE_24` | Quota degli elementi interattivi visibili con area almeno 24x24 px CSS, oppure più piccoli ma il cui cerchio di 24 px di diametro, centrato sull'elemento, non tocca altri bersagli (l'eccezione di spaziatura di WCAG 2.2 SC 2.5.8, AA; le altre eccezioni del criterio, controllo equivalente, user agent ed essenziale, non sono valutate). Si misura con `getBoundingClientRect()` nel viewport del profilo; i link inline nel testo sono esclusi; al più 1.500 bersagli per pagina | DOM | % | Provv. `70→0` `95→100`, quote editoriali | tutti | Forte (cat.; norma); soglie di quota editoriali |
| `FAI.TARGET_SIZE_44` | Quota degli elementi interattivi con almeno 44x44 px (SC 2.5.5, AAA), senza eccezione di spaziatura. web.dev raccomanda 48x48 px con 8 px di spaziatura: la v1 raccoglie anche la quota sotto 48 px ma non la valuta | DOM | % | Provv. `30→0` `80→100`, quote editoriali | tutti | Moderata (cat.) |
| `FAI.A11Y_BASIC` | Problemi base di accessibilità: immagini visibili senza `alt` (`a11y.img_missing_alt`; escluse le decorative: `role=presentation` o `none`, `aria-hidden`) più 1 se manca `lang` (`a11y.lang_missing`). Le etichette dei campi non si contano qui: hanno una sola casa in `CCL.FORM_LABELS` (4.8). È un sottoinsieme integrato; axe-core completo è fase 2 | DOM | conteggio | Provv. `0→100` `5→60` `20→0`, editoriali (conteggio di `alt` e `lang`). WebAIM Million 2026: il 95,9% delle home non rispetta WCAG 2, media 56,1 errori per pagina, `alt` mancante nel 53,1%; i siti di shopping 71,0 errori per pagina. L'automazione rileva circa il 57% dei problemi per volume (studio vendor Deque) | tutti | Forte (cat.; norma); copertura parziale |
| `FAI.OVERLAY_INTERRUPTIONS` | Numero di overlay o popup che interrompono la pagina: un modale (`role=dialog`, `aria-modal`, `dialog`) o un overlay che copre almeno il 15% del viewport (`interrupting`). Le barre di consenso più piccole sono elencate ma non interrompono; la testata o la navigazione fissa non è un overlay. L'evidenza riporta il tipo (consenso, newsletter, promo, login, chat, verifica età, carrello) | DOM | conteggio | Provv. `0→100` `1→80` `2→50` `4→0`, editoriali. NN/g: evitare modali per newsletter e checkout. Google esenta i dialoghi cookie ed età richiesti dalla legge ma penalizza gli interstitial invasivi | tutti | Moderata (cat.) |
| `FAI.OVERLAY_COVERAGE` | Copertura massima del viewport da parte di un overlay che interrompe (0-100) | DOM | % | Provv. `15→100` `30→60` `60→20` `90→0`, editoriali. Linee guida Google sugli interstitial invasivi | tutti | Moderata (cat.) |

#### 4.2.5 Journey (9 KPI)

Esistono solo se è stato eseguito un journey con obiettivo e oracolo (sezione 5.3). Aggregazione `first` salvo `FAI.JOURNEY_SUCCESS` (`all`).

| ID | Definizione | Metodo | Unità | Ancore v2 e fonte | Stadi | Evidenza |
|---|---|---|---|---|---|---|
| `FAI.JOURNEY_SUCCESS` | Obiettivo raggiunto secondo l'**oracolo indipendente** (set chiuso: `cart_contains_item_under_price`, `cart_not_empty`, `pdp_reached`, `search_results_shown`), mai secondo lo stato DONE dell'agente. Un oracolo che passa conta sempre. Senza un oracolo che passa il KPI è non valutabile, perché il fallimento non è del sito, quando il percorso finisce in un errore del sistema di prova (`journey_error`), su una pagina che il browser non ha caricato (`navigation_error`: DNS, TLS, proxy o host morto finiscono sulla stessa pagina e la run non sa distinguerli; la pagina iniziale inclusa, con la run `failed`; l'errore di rete è registrato e si ripete la run), dopo che la guardia ha trattenuto controlli di una pagina che l'audit non ha saputo leggere (`page_unreadable`), dopo che Jev ha scelto TYPE_TEXT e nessun aiuto testuale ha potuto scriverlo (`text_helper_unavailable`) o la guardia ha rifiutato il testo che aveva scritto (`text_refused`: il percorso scelto dal pilota è stato negato), su una pagina anti-bot (`bot_challenge`) o quando l'esito non si legge senza una congettura (`cart_unreadable`, `final_page_unreadable`, `cart_items_unreadable`, `cart_price_unreadable`, `cart_price_ambiguous`) | JNY più oracolo | bool | Pubbl. `true→100` `false→0`. MeasuringU: media 78% su 1.189 compiti di usabilità, oltre 92% nel quartile alto e sotto 49% in quello basso. Un fallimento può dipendere dall'agente: ripetere (sezione 7) | journey | Moderata (cat.; esito standard, ma validità dell'agente limitata) |
| `FAI.ACTIONS_TO_GOAL` | Interazioni (click, digitazioni, selezioni) eseguite per raggiungere l'obiettivo, solo se l'oracolo lo conferma. Informativo, peso 0 | JNY | conteggio | Provv. nessuna ancora: è valutato tramite `FAI.ACTIONS_RATIO` | journey | Debole (cat.) |
| `FAI.ACTIONS_RATIO` | Interazioni eseguite diviso percorso minimo, dichiarato da chi avvia il journey (`optimal_steps`; senza minimo noto è `not_applicable`), solo se l'obiettivo è raggiunto. La ricerca definiva l'efficienza come minimo diviso effettivo (valore ≤1): qui il rapporto è l'inverso (≥1) | JNY | rapporto | Provv. `1→100` `2→50` `3→0`, editoriali. Nessuna regola dei "3 click": Porter (UIE, 44 utenti, 620 compiti, oltre 8.000 click) non trovò più abbandoni a 3 click che a 12 | journey | Debole (cat.; gli agenti sono più diretti degli umani: AgentA/B) |
| `FAI.TIME_ON_TASK_SITE` | Tempo attribuibile al sito: somma, sui passi eseguiti, del tempo di esecuzione dell'input e di quello di assestamento (azione→assestamento), esclusi la latenza di decisione (host o modello), la generazione del testo e la pausa di WAIT; solo se l'obiettivo è raggiunto. Il cronometro parte dopo il controllo di freschezza dell'harness (una lettura di `snapshot.js`), subito prima dell'input. Solo questa parte è confrontabile con gli umani | JNY | ms | Provv. `5000→100` `20000→50` `60000→0`, editoriali e dipendenti dal compito | journey | Debole (cat.) |
| `FAI.DEAD_CLICK_RATE` | Quota dei click senza effetto visibile. Un effetto è una mutazione del DOM (i nodi che si aggiornano da soli, come un orologio, esclusi), una navigazione, una scheda aperta, un altro sito o una pagina non caricata, uno spostamento di layout causato dall'input, un testo visibile cambiato senza alcuna mutazione (feedback solo CSS) o un cambio di ciò su cui l'azione può incidere (documento, URL, scroll, viewport, valori dei campi). Una richiesta da sola non è un effetto, un beacon di tracciamento compreso: le richieste sono evidenza. Contano solo i click su elementi con ruolo button o link e solo quelli con una misura, per evitare falsi positivi da click esplorativi. Il KPI è sottostimato per costruzione: un click il cui unico effetto è un download o un cambio di focus risulta morto, e un'animazione CSS che cambia il testo visibile nasconde un click morto | JNY | % | Provv. `0→100` `10→60` `30→0`, editoriali. Clarity: "nessun feedback in un tempo ragionevole"; FullStory: valori esatti proprietari | journey | Moderata (cat.; definizione di settore); soglie editoriali |
| `FAI.RAGE_EVENTS` | Stesso target agito almeno 3 volte consecutive senza cambiamento di stato. È un analogo del rage click: l'agente agisce ogni pochi secondi, quindi la finestra di Contentsquare (almeno 3 click in meno di 2 s, da sintesi [S]) non scatterebbe mai. Le WAIT in mezzo non contano né interrompono la sequenza; un evento per sequenza. Con policy `typesafe` l'Agent si ferma dopo tre azioni consecutive senza cambiamento, quindi il conteggio è troncato lì (la run lo segnala con un avviso); la policy `host` è avvisata nelle note della guardia e prosegue entro il budget di passi | JNY | conteggio | Provv. `0→100` `1→50` `3→0`, editoriali | journey | Debole (cat.; inferenza) |
| `FAI.BACKTRACK_RATE` | Ritorni a pagine già viste: (S − N) / S, con S visite totali e N pagine uniche. Le visite sono gli URL web (frammento escluso, query ordinata, pagina di errore del browser esclusa) prima e dopo ogni passo eseguito e dopo ogni record `NAVIGATION`, con le ripetizioni consecutive collassate | JNY | % | Provv. `0→100` `20→60` `50→0`. Nessuno standard | journey | Debole (cat.) |
| `FAI.LOSTNESS` | Lostness di Smith (1996): L = √[(N/S − 1)² + (R/N − 1)²], con N pagine uniche, S visite totali, R minimo di pagine necessarie. Calcolata solo se R (pagina iniziale inclusa) è noto e il journey è riuscito (un percorso fallito non è un percorso verso l'obiettivo); R è limitato a N e il risultato a 1. Da 0 (efficiente) a 1 (perso) | JNY | 0-1 | Pubbl. `0→100` `0.4→60` `0.5→40` `1→0`. Smith: sotto 0,4 non perso, 0,4-0,5 indeterminato, sopra 0,5 perso; replicazione MeasuringU: il 91% di chi stava sotto 0,4 non era perso, l'89% di chi stava sopra 0,5 lo era. Formula da sintesi secondarie: l'articolo originale non è stato aperto, la variante di Frontiers 2020 è diversa | journey | Moderata (cat.) |
| `FAI.UNEXPECTED_NAV` | Una scheda aperta dal passo; un nuovo documento o un altro sito dopo un passo che non era un CLICK o SELECT scelto (digitazione, scorrimento, attesa); una pagina che naviga da sola tra due passi (record `NAVIGATION`: un redirect, un refresh o uno script mentre la policy decide). Un click scelto verso un altro sito è solo evidenza: chiude il journey. Una navigazione vista subito dopo un passo con assestamento scaduto (`follows_timeout`) è una visita, non è contata | JNY | conteggio | Provv. `0→100` `1→50` `3→0`, editoriali. Nessuno standard | journey | Debole (cat.) |

#### 4.2.6 Riferimenti Baymard e NN/g per la frizione

Le cifre Baymard cambiano con l'anno del benchmark e con la definizione: si cita l'anno quando la ricerca lo riporta (2024 per i campi del checkout e i passi, 2025 per il benchmark del checkout); altrimenti la cella dice "anno non indicato nella ricerca". Le quote della lista degli abbandoni (17%, 18%, 19%, 13%, 9%, 20%, 40%, 12%) vengono da una pagina senza data né dimensione del campione (sezione 3.2).

| Area | Dato | Fonte |
|---|---|---|
| Pagina prodotto | 52% dei siti desktop e 62% dei siti mobile sono "mediocri o peggio"; nessuna stima del costo totale dell'ordine 67%; nessuna politica di reso in PDP 44%; taglie non scelte con pulsanti 57%; nessuna immagine "in scala" 37%; nessun prezzo per unità 81% | Baymard, pagine prodotto (anno non indicato nella ricerca) |
| Listing | Problemi seri nell'80% dei siti; non offrono tutti e 5 i filtri essenziali 57%; non offrono tutti e 4 gli ordinamenti essenziali 64%; il pulsante Indietro non rispetta le aspettative 59%; nessuna panoramica dei filtri applicati 28% | Baymard, liste prodotti (anno non indicato nella ricerca) |
| Ricerca | Autocomplete nell'80% dei siti (19% al livello alto); nessun suggerimento tollerante agli errori 69%; nessun pulsante di invio esplicito su mobile 21%; ricerca "mediocre o peggio" 56% | Baymard, ricerca (anno non indicato nella ricerca) |
| Checkout 2025 (oltre 180 siti) | Ospite non più evidente 47%; account non rinviato a dopo l'ordine 42%; un solo metodo di pagamento 21%; nessuna validazione inline 31%; nessuna ricerca di indirizzo 55%; "velocità di spedizione" al posto di "data di consegna" 41%; "mediocre o peggio" 64% | Baymard, checkout |
| Navigazione | Home e categorie: 58% desktop e 67% mobile mediocri o scarsi; il 60% dei menu a comparsa è scarso; ritardo di hover consigliato 300-500 ms | Baymard: **solo secondario** (sintesi di ricerca; anno non indicato nella ricerca) |
| Ricerca, uso | Primo tentativo riuscito 51%, secondo 32%, terzo 18% | NN/g, ricerca |
| Pagina prodotto, elementi minimi | Nome, immagini con ingrandimento, prezzo comprensivo dei costi aggiuntivi, opzioni chiare, disponibilità, aggiunta al carrello con conferma, descrizione concisa | NN/g, pagine prodotto |

### 4.3 TRI: fiducia e riduzione del rischio (13 KPI, peso 20)

Evidenza di riferimento: linee guida di Stanford (percezioni, non acquisti) e numeri Baymard sui motivi di abbandono (19% sfiducia sulla carta, 13% resi, 9% pochi metodi di pagamento, 20% consegna lenta).

| ID | Definizione | Metodo | Unità | Ancore v2 e fonte | Stadi | Evidenza |
|---|---|---|---|---|---|---|
| `TRI.HTTPS` | HTTPS su tutte le pagine del percorso, letto dall'URL finale di ogni pagina (aggregazione `all`). È igiene di base, non prova di sicurezza | DOM (URL finale) | bool | Pubbl. `true→100` `false→0`. Baymard: il 19% non si fida del sito per i dati della carta | tutti | Forte (igiene) |
| `TRI.MIXED_CONTENT` | Sottorisorse `http://` (o segnalate da Chrome come contenuto misto) in un documento `https` | CDP | conteggio | Provv. `0→100` `1→50` `5→0`, editoriali | tutti | Forte (igiene) |
| `TRI.CONTACT_INFO` | Almeno un contatto tra email, telefono, indirizzo (`mailto:`, `tel:`, regex) | DOM | bool | Pubbl. `true→100` `false→0`. Stanford, linee guida 2, 4, 5 (organizzazione reale, persone, contatto facile) | tutti | Moderata |
| `TRI.LEGAL_ID` | Identificativo legale visibile (P.IVA o numero di registro) | DOM | bool | Pubbl. `true→100` `false→0`. Stanford, linea guida 2. `anchors.json` cita la direttiva sull'e-commerce, art. 5, e l'obbligo italiano di indicare la P.IVA; il catalogo precisa la direttiva 2000/31/CE art. 5, par. 1, lett. g (numero di identificazione IVA), recepita dall'art. 7 del D.Lgs. 70/2003. La precisazione non compare nei file di ricerca, che segnano come [M] che la legge italiana richieda tali identificativi: va confermato su EUR-Lex, e se non è confermato il KPI passa a Provv. | tutti | Moderata |
| `TRI.POLICY_LINKS` | Quanti tra resi, spedizioni, privacy e termini hanno un link visibile in pagina (etichetta o percorso dell'URL del lessico), da 0 a 4; massimo tra le pagine. Non misura i click necessari per raggiungere la pagina dei resi | DOM | conteggio | Provv. `0→0` `2→50` `4→100`. Baymard: il 13% abbandona per la politica di reso; punti editoriali | tutti | Moderata |
| `TRI.JSONLD_RETURN_POLICY` | `hasMerchantReturnPolicy` nei dati strutturati JSON-LD | DOM | bool | Provv. `true→100` `false→0`. Google lo elenca tra le proprietà raccomandate di Product e Offer (merchant listing) [V]. È leggibile da macchina: non prova che l'utente veda la politica | pdp | Moderata; debole (cat.) come effetto sull'utente |
| `TRI.JSONLD_SHIPPING` | `shippingDetails` nei dati strutturati JSON-LD | DOM | bool | Provv. `true→100` `false→0`. Stessa fonte Google | pdp | Moderata; debole (cat.) come effetto sull'utente |
| `TRI.PAYMENT_LOGOS` | Metodi di pagamento riconoscibili e distinti (marchi del lessico trovati in `alt`, `title`, nome del file, `aria-label`, classi e brevi etichette di testo: Visa, Mastercard, PayPal, Klarna...); massimo tra le pagine | DOM | conteggio | Provv. `0→0` `1→40` `3→100`. Baymard: il 9% abbandona per pochi metodi di pagamento; i marchi noti sono i più fidati, molte persone non hanno preferenze [S] | tutti | Debole o moderata |
| `TRI.REVIEWS_PRESENT` | Recensioni sul prodotto (`aggregateRating` o valutazione visibile; stelle con `aria-label`) | DOM | bool | Pubbl. `true→100` `false→0`. Spiegel Research Center (osservazionale [S]) | pdp | Moderata o forte (osservazionale) |
| `TRI.REVIEW_COUNT` | Numero di recensioni | DOM | conteggio | Pubbl. `0→0` `5→70` `20→100`. Spiegel: cinque recensioni alzano la probabilità di acquisto del 270% rispetto a nessuna, con rendimenti decrescenti oltre le prime cinque [S]; il punto a 20 è editoriale | pdp | Moderata o forte (osservazionale; fonte secondaria) |
| `TRI.RATING_BAND` | Fascia della valutazione: 4.0-4.7, 4.8-5.0, 3.5-3.9, <3.5 | DOM | enum | Pubbl. `4.0-4.7→100` `4.8-5.0→70` `3.5-3.9→50` `<3.5→0`. Spiegel: picco della probabilità di acquisto tra 4,0 e 4,7 stelle [S]; i punteggi delle altre fasce sono editoriali | pdp | Moderata o forte (osservazionale; fonte secondaria) |
| `TRI.DELIVERY_TIME_STATED` | Tempi di consegna indicati prima del checkout ("consegna entro...", data). Non misura la velocità reale della consegna. Nel carrello vuoto: non valutato (`empty_cart`) | DOM | bool | Pubbl. `true→100` `false→0`. Baymard: il 20% abbandona per consegna lenta; il 41% dei siti usa "velocità di spedizione" invece di "data di consegna" (2025) | pdp, cart | Moderata |
| `TRI.RETURNS_CLARITY` | Chiarezza della politica di reso: `clear` (finestra temporale più almeno una condizione concreta), `vague`, `absent`. Rubrica `returns_clarity` | LLM (giudice Jev) su snippet `returns_policy` | label | Provv. `clear→100` `vague→40` `absent→0`. Baymard: il 13% abbandona per la politica di reso e il 44% dei siti non la mostra in PDP (anno non indicato nella ricerca); punteggi editoriali | tutti | Moderata (cat.) |

### 4.4 PTI: trasparenza di prezzi e costi (8 KPI, peso 15)

Evidenza di riferimento: Baymard, 40% di abbandoni per costi extra e 12% per totale non visibile (il 40% esclude chi "stava solo guardando"), 67% delle PDP senza stima del costo totale dell'ordine (anno non indicato nella ricerca).

| ID | Definizione | Metodo | Unità | Ancore v2 e fonte | Stadi | Evidenza |
|---|---|---|---|---|---|---|
| `PTI.PRICE_VISIBLE_PDP` | Prezzo visibile above the fold in PDP (il centro del prezzo nella prima schermata, vedi l'apertura della sezione 4): il prezzo principale letto dal DOM visibile (`[itemprop=price]` o regex di valuta), fuori dalle schede di prodotti correlati, dagli overlay e da testata, navigazione e piè di pagina, il più vicino all'`h1` tra quelli above the fold (se nessuno lo è, il più vicino in assoluto); i dati strutturati non lo rendono visibile (sono in `PTI.PRICE_JSONLD_MATCH`) | DOM | bool | Pubbl. `true→100` `false→0`. NN/g: il prezzo, comprensivo dei costi aggiuntivi, è un elemento minimo della pagina prodotto | pdp | Moderata (cat.) |
| `PTI.PRICE_JSONLD_MATCH` | Prezzo strutturato coerente con quello visibile (aggregazione `all`). Per la ricerca una discrepanza è un difetto di fiducia. Stessa valuta e differenza al più 0,01; senza prezzo strutturato o visibile è `not_applicable` | DOM | bool | Pubbl. `true→100` `false→0`. Riferimento: documentazione Google su Product e `offers.price` | pdp | Debole (cat.; effetto sull'utente) |
| `PTI.SHIPPING_COST_PRE_CHECKOUT` | Costo di spedizione visibile prima del checkout: un testo di spedizione in PDP con un importo o una dicitura di spedizione gratuita, oppure una riga o un valore di spedizione nel carrello (un carrello vuoto non dice nulla: `empty_cart`). Un rinvio al checkout ("Spese di spedizione calcolate al momento del pagamento", "Shipping calculated at checkout", chiave di lessico `shipping_deferred`) o una parola di spedizione senza costo ("Shipping country") non mostrano alcun costo | DOM | bool | Pubbl. `true→100` `false→0`. Baymard: 40% abbandona per costi extra (spedizione, tasse, commissioni), 12% non vede il totale. Un dato più vecchio (64% cerca il costo di spedizione in PDP) è solo secondario, non riletto | pdp, cart | Forte (cat.) |
| `PTI.FREE_SHIPPING_THRESHOLD` | Soglia di spedizione gratuita comunicata | DOM (lessico) | bool | Provv. `true→100` `false→0`. Convenzione editoriale: dichiarare la soglia riduce la sorpresa di costo. Vedi la nota di proprietà con `MPI.RECIPROCITY` (4.8) | tutti | Debole (cat.) |
| `PTI.VAT_STATED` | IVA inclusa o esclusa dichiarata ("IVA inclusa", "incl. VAT") | DOM (lessico) | bool | Provv. `true→100` `false→0`. La ricerca la tratta come indicatore binario con lessico; `anchors.json` cita la direttiva 98/6/CE, non verificata nei file di ricerca. La norma richiede un prezzo al consumatore comprensivo di imposte, non una dicitura "IVA inclusa": il KPI misura un segnale di informazione, quindi l'ancora è editoriale | pdp, cart, checkout | Debole (cat.) |
| `PTI.FUNNEL_PRICE_DELTA` | Aumento non spiegato (%) del totale del carrello e del primo step del checkout rispetto al prezzo dell'articolo aggiunto in PDP per la sua quantità, al netto della spedizione dichiarata e di un'imposta aggiunta in cima (un'IVA già inclusa non aggiunge nulla); vale il massimo tra i passi. Il carrello è la base: senza PDP e carrello il KPI non è valutato (3.5, regola c), e un checkout illeggibile accorcia solo la catena (`steps_skipped`); senza totale si confronta il subtotale; carrello vuoto: `empty_cart`. La ricerca assegna 100 se Δ = 0 o se le voci erano dettagliate prima, e scala con Δ% | DOM | % | Provv. `0→100` `2→60` `10→0`, editoriali. Baymard: 12% non vede il totale, 40% costi extra | pdp, cart, checkout | Forte (cat.; costrutto) |
| `PTI.STRIKETHROUGH_LOWEST30` | Per ogni prezzo barrato, presenza della dicitura "prezzo più basso degli ultimi 30 giorni" (Omnibus). Senza prezzo barrato è "non applicabile". Controlla la dicitura, non lo storico reale dei prezzi. Una dicitura fuori dalle schede prodotto copre anche le schede, una dentro una scheda copre solo le schede, un barrato fuori dalle schede richiede una dicitura fuori dalle schede; senza i marcatori `in_card` di `audit.js` basta una dicitura in pagina (`statement_scope: unknown`). I prezzi barrati si contano su tutta la pagina (`price_counts` di `audit.js`), anche oltre i primi 80 prezzi che il payload elenca | DOM (lessico) | bool | Pubbl. `true→100` `false→0`. Art. 6a Direttiva sull'indicazione dei prezzi, dal 28 maggio 2022 [S]; CGUE C-330/23 (26 settembre 2024): la riduzione percentuale si calcola sul prezzo più basso dei 30 giorni [S]. `anchors.json` indica la direttiva (UE) 2019/2161 (Omnibus, che introduce l'art. 6a), un numero che non compare nei file di ricerca e non è verificato | tutti | Forte (cat.; norma; fonte secondaria) |
| `PTI.UNEXPLAINED_FEES` | Voci di costo (importo positivo) nel carrello o nel primo step del checkout che la PDP non annuncia: la spedizione è spiegata da qualunque dicitura di spedizione o soglia di spedizione gratuita in PDP, un'altra voce dalle sue parole presenti in PDP. Sono escluse le voci già contate da DPR (opzioni pre-selezionate, articoli aggiunti di nascosto) e le imposte (IVA, MwSt, VAT...; dazi e tasse doganali sono costi, non imposte). Carrello vuoto: `empty_cart` | DOM (snippet `fee_line`) | conteggio | Provv. `0→100` `1→40` `2→0`, editoriali. Baymard: 40% abbandona per costi extra | cart, checkout | Forte (cat.; costrutto) |

### 4.5 CCL: chiarezza e carico cognitivo (9 KPI, peso 15)

| ID | Definizione | Metodo | Unità | Ancore v2 e fonte | Stadi | Evidenza |
|---|---|---|---|---|---|---|
| `CCL.CTA_SALIENCE` | Salienza della CTA primaria: composito 0-100 = `100 x fold x (0,5 C + 0,25 A + 0,25 U)`, con C il contrasto del testo scalato da 3:1 (0) a 4,5:1 (1), A l'area fino a 10.000 px² (circa 225x44 px), U = 1/n con n le CTA con aspetto primario above the fold (i pulsanti delle schede prodotto contano una volta sola, vedi `CCL.PRIMARY_CTA_COUNT`), fold = 1 se il centro della CTA è nella prima schermata (above the fold, vedi l'apertura della sezione 4) e 0,5 altrimenti, anche quando ne spunta solo il bordo superiore. In PDP è l'aggiunta al carrello (o l'acquisto immediato), nel carrello la CTA di checkout (carrello vuoto: `empty_cart`; CTA non riconosciuta: `checkout_cta_not_found`, mai un 0); prodotto esaurito: `not_applicable:out_of_stock`. Contrasto WCAG 1.4.3 (4,5:1 per il testo) e 1.4.11 (3:1 per elementi non testuali) [M]; il prior della ricerca per il solo contrasto era lineare da 3:1 (0) a 7:1 (100) | DOM (stili calcolati) | punteggio | Provv. `20→0` `50→50` `80→100`, editoriali | pdp, cart | Debole o moderata |
| `CCL.PRIMARY_CTA_COUNT` | Numero di CTA primarie in competizione above the fold (ciascuna con il centro nella prima schermata, vedi l'apertura della sezione 4). I pulsanti delle schede prodotto ripetono la stessa azione su ogni articolo (l'«Aggiungi al carrello» di ogni scheda di un listing, una fila «Completa il look»): insieme contano come una CTA sola. Fogg: una CTA dominante per fase | DOM | conteggio | Provv. `1→100` `2→70` `4→20` `6→0`, editoriali | tutti | Debole o moderata |
| `CCL.CHOICE_SUPPORT` | Assortimenti ampi (almeno 24 schede) accompagnati da filtri o ordinamento. Un assortimento piccolo non è penalizzato. Aggregazione `all` | DOM | bool | Provv. `true→100` `false→0`. Soglia di 24 schede editoriale: nessuna soglia validata in letteratura (l'esperimento di Iyengar e Lepper usava 24 contro 6 opzioni). Evidenza mista (sezione 2.3) | plp | Debole o moderata |
| `CCL.INFO_SCENT_GENERIC` | Quota dei link di navigazione con etichette generiche ("clicca qui", "scopri di più") | DOM (lessico) | % | Provv. `0→100` `10→70` `30→20` `50→0`, editoriali. Pirolli e Card 1999 [S] | tutti | Moderata (la teoria è solida, il punteggio concreto varia) |
| `CCL.READABILITY` | Leggibilità del testo: Gulpease per l'italiano (89 − 10·lettere/parole + 300·frasi/parole), Flesch per l'inglese, sul testo corrente della pagina (almeno 30 parole, altrimenti `not_applicable:little_prose`) e nella lingua della pagina: quella che dichiara, salvo un testo che si legge nella lingua della run (vedi 5.5; le altre lingue sono `unsupported_language`) | DOM (conteggi di parole, frasi, lettere) | punteggio | Provv. `40→0` `60→60` `80→100`: soglie pubblicate per l'italiano (i punteggi 0/60/100 sono editoriali); sulle pagine inglesi gli stessi punti sono applicati al Flesch, che ha bande proprie: soglia trasferita a una grandezza diversa, non validata; il flag è unico per KPI (ancore per lingua: sezione 8). Gulpease: sotto 80 difficile per la licenza elementare, sotto 60 per la media, sotto 40 per il diploma [S]. Il prior della ricerca era 60→50. Flesch sulle stesse ancore è una scelta non validata nei file di ricerca | home, plp, pdp | Forte (misura); debole come predittore di engagement |
| `CCL.HEADINGS` | Un solo `h1` e almeno un `h2` (aggregazione `all`) | DOM | bool | Provv. `true→100` `false→0`. Convenzione editoriale | tutti | Debole (cat.) |
| `CCL.FORM_LABELS` | Quota dei campi visibili con etichetta associata (`forms.labels_share`, in percentuale). Conta come etichetta ciò che riconosce `audit.js` (`label[for]`, etichetta che avvolge il campo, `aria-label`, `aria-labelledby`, `title`). Valutato solo se la pagina ha campi visibili, altrimenti non applicabile (`not_applicable:no_form_fields`). È l'unica casa delle etichette dei campi (4.8). Aggregazione `min` | DOM | % | Provv. `50→0` `90→60` `100→100`, editoriali. Sforzo mentale: etichette legate agli input (regola axe `label`); `anchors.json` cita WCAG 3.3.2 (etichette o istruzioni), non verificato nei file di ricerca. Contesto (non è un'ancora): WebAIM Million 2026, etichette mancanti nel 51% delle home | tutti | Moderata (cat.) |
| `CCL.VISUAL_COMPLEXITY` | Complessità visiva dallo screenshot: composito 0-100 = `100 x (0,40 E + 0,35 B + 0,25 C)`, con E la densità dei bordi / 0,25, B i byte per pixel dell'immagine compressa (JPEG a qualità 75) / 0,40 e C la colorfulness di Hasler-Süsstrunk / 100, ciascuno limitato a 1; lo screenshot è riportato a pixel CSS. Senza screenshot: `no_screenshot` | IMG | punteggio | Provv. `30→100` `60→60` `90→0`, editoriali: penalizza solo i valori alti. Reinecke et al. 2013: modelli su valutazioni di complessità e colorfulness **percepite** (sezione 2.7), non sulla conversione. La ricerca non ammette una complessità "ideale". La colorfulness di Hasler-Süsstrunk predice la colorfulness percepita e non la complessità percepita (il cui modello usa quadtree e scomposizione spaziale): nel composito è un proxy debole, e il KPI resta provvisorio con peso 0,5 | tutti | Debole o moderata (conversione); forte (percezione, grado della ricerca per l'estetica) |
| `CCL.VALUE_PROP_CLARITY` | Chiarezza della proposta di valore: `clear` (dice che cosa si vende e almeno un beneficio concreto), `partial`, `unclear`. Rubrica `value_prop`, applicata a home, plp e pdp | LLM (giudice Claude) su snippet `headline` e `cta` | label | Provv. `clear→100` `partial→50` `unclear→0` | tutti | Debole (cat.) |

### 4.6 MPI: offerta di motivatori genuini (5 KPI, peso 10)

Si accreditano solo le versioni genuine. Per scarsità e urgenza vale il credito condizionato: se il relativo segnale DPR è scattato (confidenza almeno 0,5), il KPI è punteggiato come se il segnale fosse assente, con motivo `credit_withheld:<id DPR>` (sezione 3.3, punto 7).

| ID | Definizione | Metodo | Unità | Ancore v2 e fonte | Stadi | Evidenza |
|---|---|---|---|---|---|---|
| `MPI.SCARCITY_SIGNALS` | Segnali di scarsità genuini: testi di scarsità del lessico IT/EN (per esempio "ultimi pezzi"), distinti, sulle pagine raggiunte, più 1 se `availability` è `LimitedAvailability` nei dati strutturati. Non accreditati se `DPR.FAKE_LOW_STOCK` è scattato | DOM (JSON-LD e lessico) | conteggio | Provv. `0→30` `1→80` `2→100`. Barton et al. 2022 [S]. L'assenza vale 30 e non 0: è un segnale facoltativo (scelta editoriale) | tutti | Moderata |
| `MPI.URGENCY_SIGNALS` | Segnali di urgenza con scadenza dichiarata. Un "sta per finire" senza scadenza non conta (Mathur: informazione nascosta), né un testo orario che il test del countdown ha visto fermo (il tempo di una gara, la durata di un video). Non accreditati se `DPR.COUNTDOWN_RESET` è scattato | DOM (lessico e timer) | conteggio | Provv. `0→30` `1→80` `2→100`. Mathur 2019: urgenza con scadenza dichiarata; punti editoriali | tutti | Moderata (riga scarsità della ricerca) |
| `MPI.RECIPROCITY` | Segnali di reciprocità: benefici distinti offerti gratis (omaggi, campioni, resi gratuiti, sconto per l'iscrizione); lo stesso beneficio scritto in modi diversi conta una volta e la soglia di spedizione gratuita resta in PTI (vedi la nota di proprietà in 4.8) | DOM (lessico: "omaggio", "gratis", "campione") | conteggio | Provv. `0→0` `1→70` `2→100`. Cialdini; la ricerca la classifica come proxy debole o moderato | tutti | Debole o moderata (solo proxy) |
| `MPI.AUTHORITY` | Segnali di autorità: `present` (ente, premio, testata o esperto nominati, o numero di certificazione), `weak` (affermazioni generiche), `absent`. Rubrica `authority` | LLM (giudice Jev) su snippet `authority` | label | Provv. `present→100` `weak→50` `absent→0` | tutti | Debole o moderata |
| `MPI.SOCIAL_PROOF_RICH` | Riprova sociale oltre al punteggio a stelle: `rich` (recensione o testimonianza con testo specifico e un elemento di autenticità), `basic`, `absent`. Rubrica `social_proof`, applicata a home, plp, pdp e cart | LLM (giudice Jev) su snippet `testimonial` | label | Provv. `rich→100` `basic→60` `absent→0`. Spiegel (effetto delle recensioni, osservazionale [S]) | tutti | Moderata o forte (osservazionale) |

### 4.7 DPR: risk signals di dark pattern (9 KPI, solo penalità)

Unità di tutti i DPR: **confidenza** da 0 a 1 che il pattern sia presente. Il contributo al rischio è severità x confidenza; i segnali si combinano con noisy-OR (sezione 3.4). Aggregazione `max` sul sito. I test deterministici danno confidenze graduate (da 0 a 1,0, secondo la scala di ogni riga: 0,3, 0,4, 0,6, 0,8, 0,9, 1,0); per un segnale giudicato "presente" la confidenza è 0,8 moltiplicato per la quota di accordo tra i giudici (1,0 per un campione di Jev, che è un solo campione e vale come tre campioni concordi di Claude: sezione 6.1, punto 4). Mai un credito: i DPR non entrano nei sotto-indici.

| ID | Definizione | Test di inganno e metodo | Severità | Stato, fonte, evidenza | Stadi |
|---|---|---|---|---|---|
| `DPR.COUNTDOWN_RESET` | Countdown che si azzera al ricaricamento | DEC. Un testo orario (h:mm:ss) è un timer solo se scorre: letto due volte a 1,5 s di distanza nella stessa visita, il suo tempo residuo cala di circa il tempo trascorso (altrimenti è «statico»: il tempo di una gara, la durata di un video). La PDP del funnel è caricata in due contesti isolati freschi dello stesso profilo a almeno 10 s l'uno dall'altro: un conto alla rovescia a scadenza fissa mostra un residuo più piccolo del tempo trascorso (tolleranza max(2 s, 20%)), uno che riparte no. I timer delle due visite sono abbinati per testo con le cifre mascherate (per posizione solo se ogni visita ne mostra uno e le parole coincidono); si confronta ogni coppia e si riporta la peggiore, e senza coppie il test è non valutato (`countdown_not_paired`). Confidenza 1,0 se il residuo cala al più della tolleranza o cresce (il timer riparte), 0,8 se cala di più ma meno del tempo trascorso (la scadenza si è spostata). Il secondo criterio di Mathur, l'offerta ancora valida a scadenza, non è verificato. Limiti: un conto alla rovescia lato server dietro HTML in cache sembra un riavvio (come in Mathur); timer che si aggiornano meno spesso di ogni 1,5 s circa, che partono solo quando sono visibili o disegnati su canvas o immagine, e timer a soli minuti sono letti come assenti o statici, mai come rischio | 0,9 | Pubbl. Mathur 2019 [V]: 393 countdown su 361 siti, 157 istanze ingannevoli su 140 siti. Evidenza forte (cat.) | tutti |
| `DPR.FAKE_LOW_STOCK` | Messaggio di scorte basse incoerente tra visite o prodotti | DEC. Messaggio "solo N rimasti" del lessico. Tre segnali: (1) il numero cambia tra le due visite fresche senza acquisto (0,8 se cala, 0,6 se cambia altrimenti; un testo identico è normale); (2) lo stesso numero N > 1 su altri prodotti dell'elenco (0,8 su 3 o più prodotti, 0,4 su 2; N = 1 non conta mai, perché un pezzo unico dice il vero ovunque, e le varianti dello stesso prodotto, per sku o titolo, non sono altri prodotti); (3) una contraddizione con i dati strutturati del prodotto: un `inventoryLevel` diverso da N (0,8; uguale: corrobora e il prodotto esce dal conteggio dei numeri condivisi), una singola Offer `OutOfStock`, `SoldOut` o `Discontinued` (0,6). Due segnali forti indipendenti: 0,9. Una visita il cui numero può essere quello di una scheda correlata (`card_scope_unknown`) non conta tra i numeri condivisi né tra le contraddizioni. Una frase che la home o l'elenco mostrano identica fuori dalle schede prodotto (barra di annuncio, header) è testo del sito, non una dichiarazione di scorte del prodotto: esclusa da ogni visita (`site_copy`). "Rimasti solo N" seguito da un'unità di tempo ("Rimasti solo 3 giorni di saldi") non è scarsità | 0,6 | Pubbl. Mathur [V]: 632 istanze su 581 siti; su 17 siti ingannevoli, 16 decrementavano a tempo e 1 in modo casuale, alcuni con script di terze parti. Evidenza forte (cat.) | tutti |
| `DPR.SNEAK_INTO_BASKET` | Articoli aggiunti al carrello senza richiesta | DEC. Diff deterministico tra le righe del carrello e l'articolo aggiunto dal crawler (`probes.add_to_cart`): una riga con un costo che non è quell'articolo è una riga non richiesta (confidenza 1,0 se è un add-on, 0,8 altrimenti); un add-on che porta il nome dell'articolo («Protezione Scarpa Aurora 2 anni») non è l'articolo. Non valutato se non è stato aggiunto nulla, se il carrello è vuoto o ha righe non leggibili, o se l'articolo aggiunto non si riconosce tra le righe (`added_item_not_recognised`). L'aggiunta al carrello modifica lo stato del server (sessione carrello): è accettato e documentato | 0,9 | Pubbl. Mathur [V]: 7 istanze su 7 siti. Evidenza forte (cat.) | tutti |
| `DPR.PRECHECKED_PAID_ADDONS` | Opzioni a pagamento pre-selezionate | DEC. Deterministico: una casella spuntata con un prezzo positivo nella sua riga, nel carrello e nel primo step del checkout (`audit.js`). Confidenza 1,0 se è un add-on (assicurazione, garanzia, donazione, spedizione protetta: lessico `addon`); altre caselle con prezzo pre-spuntate (un voucher newsletter) sono evidenza, non rischio. Un carrello vuoto non ne mostra: non valutato | 0,6 | Pubbl. Brignull, "Preselection" [V]. `anchors.json` cita anche l'art. 22 della direttiva sui diritti dei consumatori sulle caselle pre-spuntate, non verificato nei file di ricerca. Evidenza moderata (cat.) | tutti |
| `DPR.CONSENT_ASYMMETRY` | Banner cookie: rifiutare è più difficile che accettare | DEC. Sul primo livello del banner di una home caricata di nuovo in un contesto fresco (controlli accetta, rifiuta, gestisci e chiudi, con area e aspetto). Nessun rifiuto sul primo livello: 1,0 se anche il livello che «gestisci» apre non ne ha, 0,8 se ne ha uno (un click in più) o non si è potuto leggere (`second_layer_checked` falso: si valuta solo la parte certa). Rifiuto con area sotto un quarto di quella di «accetta»: 0,8; sotto la metà: 0,6; di area simile ma con solo «accetta» riempito e più contrastato: 0,3; altrimenti 0. Su una pagina italiana la X o «Chiudi» del primo livello vale come rifiuto (`reject_kind: close`; Garante 2021: chiudere mantiene solo i cookie tecnici; EDPB e ICO non la leggono così, quindi nelle altre lingue è esclusa). Nessun controllo «accetta» riconosciuto: un'informativa con la sola chiusura vale 0, un banner con altri controlli è non valutato (`accept_not_recognised`). Nessun banner: 0 | 0,3 | Provv. EDPB: rifiutare non deve essere più difficile di accettare [S]. Nouwens et al.: togliere l'opt-out dalla prima pagina ha alzato il consenso di 22-23 punti percentuali [S]. Le soglie di rapporto e di click sono editoriali. Evidenza moderata (cat.) | tutti |
| `DPR.NAGGING_OVERLAYS` | Overlay ripetuti o insistenti | DEC. Per ogni overlay (`overlay_key`: tipo e primi 60 caratteri del testo con le cifre mascherate), le pagine del funnel che mostrano di nuovo un modale o un overlay bloccante non di consenso dopo che il crawler l'aveva chiuso in una pagina precedente della stessa scheda; e se un overlay torna dopo essere stato chiuso e la pagina ricaricata nello stesso contesto. Confidenza 0,9 se torna dopo chiusura e ricarico, 0,8 se lo stesso overlay riappare in 3 o più pagine, 0,6 in 2, altrimenti 0: overlay diversi su pagine diverse, o mostrati fino a una chiusura, sono interruzioni (`FAI.OVERLAY_INTERRUPTIONS`), non nagging (vedi 4.8). Valutato solo con una base: due pagine del funnel o il test di ricarico (`single_page`, `no_pages`). Exit-intent e popup temporizzati non sono rilevati; un testo che cambia tra le pagine oltre alle cifre (saluti personalizzati, varianti A/B) conta come un altro overlay: un segnale mancato, mai falso | 0,3 | Provv. Brignull, "Nagging" [V]; la soglia di ripetizione è editoriale. Evidenza debole (cat.) | tutti |
| `DPR.CONFIRMSHAMING` | Etichette di rifiuto colpevolizzanti | LLM binario (giudice Claude) su snippet `modal_decline` e `consent`, con citazione verbatim obbligatoria quando l'etichetta è `present`. Rubrica `confirmshaming` | 0,6 | Provv. Mathur [V]: 169 istanze su 164 siti. Evidenza moderata (cat.; rilevatore LLM) | tutti |
| `DPR.TRICK_QUESTIONS` | Caselle e domande formulate in modo ingannevole (doppie negazioni, logica invertita) | LLM binario (giudice Jev) su snippet `checkbox_label`, con citazione verbatim. Rubrica `trick_questions` | 0,6 | Provv. Mathur [V]: 9 istanze su 9 siti. Evidenza moderata (cat.; rilevatore LLM) | tutti |
| `DPR.HIDDEN_SUBSCRIPTION` | Abbonamento ricorrente non evidente | LLM binario (giudice Jev) su snippet `subscription`, `fee_line`, `cta`, con citazione verbatim. Rubrica `hidden_subscription` | 0,9 | Provv. Mathur [V]: 14 istanze su 13 siti. Lo sweep UE del 2023 segnala 23 negozi con informazioni sugli abbonamenti nascoste [S]. Evidenza moderata (cat.; rilevatore LLM) | pdp, cart, checkout |

Stadi: come nel registro `kpis.py`, i DPR non hanno vincolo di stadio; per `DPR.HIDDEN_SUBSCRIPTION` si riportano gli stadi della rubrica. Il punto del funnel in cui si conduce il test è nella colonna del test.

Per la lettura della severità: i valori 0,9, 0,6, 0,3 sono quelli del registro `kpis.py` e dell'esempio di 3.4. Valgono come "danno se il pattern è confermato": l'incertezza del rilevatore non abbassa la severità e vive solo nella confidenza (per i segnali giudicati 0,8 x accordo, quindi al massimo 0,8). La ricerca proponeva scale più basse (0,35-0,5 e 0,10-0,15). Il piano di ricalibrazione, con i valori v2 ipotizzati per i tre segnali giudicati (0,15, 0,35, 0,5), è in 3.4 e nella sezione 8.

### 4.8 Note di proprietà e sovrapposizioni residue

Il registro v1 assegna ogni KPI a un solo sotto-indice, ma alcune coppie leggono lo stesso fenomeno da due lati. Non sono errori di registro: sono punti dove la "casa unica" richiede una regola di conteggio esplicita, da rispettare in `checks.py` e `scoring.py`.

| Coppia | Rischio di doppio conteggio | Regola |
|---|---|---|
| `FAI.A11Y_BASIC` e `CCL.FORM_LABELS` | Entrambi leggono le etichette dei campi (input senza etichetta contro quota di campi con etichetta) | **Una sola casa: `CCL.FORM_LABELS`** (`forms.labels_share` in percentuale, valutato solo se la pagina ha campi visibili). `FAI.A11Y_BASIC` = `a11y.img_missing_alt` + 1 se `a11y.lang_missing`, senza etichette. Motivo: il piano approvato elenca "label dei form" in CCL e "a11y base" in FAI (la ricerca preferiva FAI, ma la sua regola vera è "una sola casa", che questa scelta rispetta); un KPI di quota con ancore proprie dice più di un conteggio sommato agli `alt`. Le ancore restano valide: 0/5/20 per il conteggio di `alt` e `lang`, 50/90/100 per la quota di etichette |
| `FAI.OVERLAY_INTERRUPTIONS`, `FAI.OVERLAY_COVERAGE` e `DPR.NAGGING_OVERLAYS` | Gli stessi overlay alimentano frizione e rischio | FAI conta l'interruzione per pagina; DPR scatta solo sulla ripetizione dopo una chiusura (pagine del funnel, ricarico), non sul singolo overlay |
| `PTI.FREE_SHIPPING_THRESHOLD` e `MPI.RECIPROCITY` | La spedizione gratuita con soglia compare in entrambi (il registro cita "spedizione/resi gratuiti" per la reciprocità) | La soglia di spedizione gratuita resta in PTI; in MPI contano omaggi, campioni, sconto per l'iscrizione, resi gratuiti, anche nello stesso blocco della soglia («Spedizione gratuita sopra 49 € · Resi gratuiti»: la soglia in PTI, i resi in MPI) |
| `PTI.UNEXPLAINED_FEES` e `DPR.SNEAK_INTO_BASKET`, `DPR.PRECHECKED_PAID_ADDONS` | Una riga "protezione spedizione" inserita senza richiesta è sia un costo non annunciato sia un articolo aggiunto di nascosto | Le voci già contate da DPR sono escluse da `PTI.UNEXPLAINED_FEES`, in coerenza con la regola "DPR segnala solo il meccanismo ingannevole" |
| `PERF.ACTION_RESPONSE_MS` e `FAI.TIME_ON_TASK_SITE` | Derivati dalle stesse finestre azione→risposta | Costrutti distinti (reattività contro sforzo totale); controllare la correlazione nell'audit di fase 2 |
| `FAI.DEAD_CLICK_RATE`, `FAI.RAGE_EVENTS`, `FAI.UNEXPECTED_NAV` | Segnali della stessa famiglia, probabilmente correlati | Come sopra: audit di correlazione (alfa di Cronbach, PCA) su un corpus di almeno 100 negozi |
| `TRI.REVIEWS_PRESENT`, `TRI.REVIEW_COUNT`, `TRI.RATING_BAND` e `MPI.SOCIAL_PROOF_RICH` | La riprova sociale di Cialdini è divisa in due case | Per costruzione: il punteggio numerico è in TRI, il contenuto più ricco (testimonianze con elementi di autenticità, giudicato) in MPI |

---

## 5. Modalità di misura

### 5.1 Due modalità

| | Audit deterministico multi-pagina | Journey con obiettivo |
|---|---|---|
| Input | URL dello shop | URL, obiettivo in linguaggio naturale, oracolo, profilo |
| Che cosa fa | Scopre il funnel con il lessico (e, dove il lessico non trova nulla, con una scelta di Jev) e raccoglie le pagine: home, listing (PLP), pagina prodotto (PDP), carrello, primo step del checkout | Un agente (Jev, o l'host se manca la chiave TypeSafe) sceglie operazione e indice tra gli elementi osservati ed esegue l'obiettivo |
| KPI prodotti | PERF per pagina, FAI strutturale, TRI, PTI, CCL, MPI deterministici; DPR deterministici; snippet per i giudizi | I KPI marcati "journey" (successo, azioni, tempo-sito, dead click, rage, backtrack, Lostness, navigazioni inattese, CLS post-input, INP sintetico, tempo di risposta alle azioni) |
| Ripetibilità | Alta per il crawl a lessico, salvo la variabilità di rete e dei contenuti del sito; uno stadio trovato dal fallback di Jev (5.2) è segnalato come non riproducibile | Né l'host né Jev (TypeSafe non offre un seed) sono riproducibili: con `typesafe` (Jev, predefinita con la chiave TypeSafe) l'esecuzione si ripete da CLI per ottenere mediane; con `host` (decisioni di Claude Code) è per l'uso interattivo |
| Vincoli | Mai ordini; stop al primo step del checkout | Gli stessi, più un oracolo di verifica indipendente |

Le due modalità alimentano gli stessi indici: i KPI di journey sono "non applicabili", e non abbassano la copertura, se il journey non è stato eseguito (sezione 3.5).

### 5.2 Audit multi-pagina

Percorso generico, senza piani per sito né valori cablati: ogni scelta nasce da elementi osservati e dal lessico IT/EN.

| Stadio | Come si trova |
|---|---|
| `home` | URL di partenza |
| `plp` | Miglior candidato tra link di navigazione e categorie, valutati con lessico, pattern di URL (per esempio `/c/`, `/categoria/`) e JSON-LD; è accettato se si classifica come listing (schede prodotto o `ItemList`); si provano al più 3 candidati e uno scartato resta come pagina `extra` |
| `pdp` | La prima scheda della PLP che si classifica come pagina prodotto acquistabile (JSON-LD Product/Offer, oppure prezzo più CTA di aggiunta). Un candidato esaurito (una singola Offer `OutOfStock`, `SoldOut` o `Discontinued`; un'aggiunta disabilitata senza un gruppo di varianti che possa abilitarla; nessuna aggiunta e testo di esaurito) resta come pagina `extra` e si prova il successivo; se sono tutti esauriti si tiene il primo, non si clicca nulla e lo stadio carrello ha motivo `out_of_stock`. Prima dell'aggiunta, un gruppo di varianti senza opzione scelta riceve la prima opzione disponibile (una scelta del codice) |
| `cart` | Click sulla CTA di aggiunta al carrello osservata e scelta con il lessico, **mai ritentato**; poi link o icona del carrello, oppure pattern di URL. Una pagina che l'aggiunta stessa ha aperto vale come carrello se lo è. Ciò che è stato aggiunto è registrato in `probes.add_to_cart` della pagina prodotto (i test di inganno lo confrontano con le righe del carrello) |
| `checkout_entry` | Dal carrello, come farebbe un nuovo acquirente: attraverso il controllo ospite del carrello quando `forms.guest_option` è vero e un controllo `guest` osservato porta a un URL di checkout dello stesso sito (o non ha `href`; un controllo che scambia solo un'opzione è seguito dalla CTA di checkout), altrimenti attraverso la CTA di checkout osservata (`probes.checkout_entry.via`: `guest` o `checkout_cta`). Si leggono i campi del primo step: `FAI.CHECKOUT_FIELDS` e la regola d sono misurati sullo step così raggiunto. **Non si compila né si invia nulla**; nessun click parte se `max_pages` non lascia posto alla pagina a cui porterebbe |

- **Classificazione di pagina:** home, plp, pdp, cart, checkout, challenge, other, con punteggi e segnali. Una pagina `challenge` (CAPTCHA o interstitial anti-bot) rende "non valutabili" gli stadi successivi con motivo `bot_challenge`: nessuna elusione oltre l'user agent e la lingua. Una pagina che non alimenta i KPI di alcuno stadio (la pagina iniziale di un journey, il carrello letto da un oracolo, una sfida anti-bot, la pagina di errore di Chrome, un candidato scartato o esaurito) è conservata come evidenza con `stage: "extra"`: nel rapporto la colonna Fase dice «nessuna (solo evidenza)» e la colonna Tipo dice che pagina è. Un click che finisce sulla pagina di errore di Chrome la conserva come `extra` e ferma il funnel (`navigation_error`); un URL di carrello che non porta a un carrello (un link "#" al mini-carrello, un redirect al login) è `extra` e lo ferma (`not_found`).
- **Sonda di autocomplete** (home): una parola presa da un'etichetta di categoria osservata è digitata nel campo di ricerca osservato, se la guardia lo consente; le voci `role=option` visibili entro 800 ms dalla fine della digitazione sono registrate in `probes.search_autocomplete`.
- **Consenso** (`settings.consent`; ogni controllo è cliccato al più una volta). Si misura prima l'atterraggio così com'è. `accept` accetta. `reject`, e `auto` su un banner che interrompe o blocca la pagina (modale, almeno il 15% del viewport, o che ne copre il centro), cliccano un rifiuto al primo livello, altrimenti "gestisci" e un rifiuto nel livello che apre, altrimenti, su una pagina italiana, la X o "Chiudi" del primo livello (Garante 2021: chiudere mantiene solo i cookie tecnici; chiudere un centro preferenze è "indietro", non un rifiuto); poi `auto` accetta un banner il cui livello attuale blocca la pagina e `reject` lo lascia. `auto` su una barra piccola che non interrompe né blocca clicca al più un controllo (un rifiuto al primo livello, altrimenti la X italiana, altrimenti nulla: `not_blocking`) e non apre mai "gestisci", che prova `deception.py` nel suo contesto. Il funnel non clicca mai sotto un banner bloccante (carrello e checkout si fermano con `consent_blocking`); `none` lascia il banner. La scelta, con i click fatti, il percorso (`via`), se il banner interrompe e se il suo livello attuale blocca, è registrata in `PageRecord.consent` della pagina che lo mostrava e fa da evidenza per `DPR.CONSENT_ASYMMETRY`. Il banner altera i byte della prima pagina e può bloccare i click. Il journey applica la stessa catena alla sua pagina iniziale prima della prima osservazione del pilota (`run_journey(consent)`, predefinito `auto` come nell'audit; 5.3): i click vanno in `steps.jsonl` con `source: "consent"` e senza numero di passo, la scelta in `PageRecord.consent` della pagina iniziale, che è solo evidenza (non alimenta `DPR.CONSENT_ASYMMETRY`).
- **Overlay.** Dopo l'accettazione di home e listing (e dei loro ricarichi), gli overlay non di consenso che interrompono (modali o newsletter e promo bloccanti) sono chiusi con il loro controllo di chiusura o rifiuto osservato, come farebbe un acquirente; le chiusure sono in `probes.dismiss_overlays` con la chiave dell'overlay e se un nuovo audit lo mostra sparito. Pagina prodotto e carrello fanno lo stesso prima del loro click.
- **Fallback di Jev** (solo con `TYPESAFE_API_KEY`; senza chiave nulla di questo esiste). Il crawl a lessico viene per primo e si ripete. Dove non trova nulla (nessun link a listing, prodotto o carrello; nessun controllo di aggiunta al carrello, di variante o di checkout), Jev è interrogato una volta per ricerca, nel modo in cui il loop dell'agente lo interroga: `model.choose` con un obiettivo generico fisso in inglese (`crawler.GOALS`, per esempio «apri la pagina del carrello, senza aggiungere né togliere articoli»; nessun testo di pagina, nessun valore di sito) sugli elementi cliccabili osservati che la `CheckoutGuard` consente e che hanno un nome accessibile. Si legge solo un target `CLICK` (`DONE` e `BLOCKED` valgono «nessun elemento»: `jev_done`, `jev_blocked`; una richiesta o una risposta non leggibile è `jev_error`). Un link è caricato come un candidato del lessico (stesso sito, nessun URL di checkout, nessun GET che cambi il carrello); un elemento senza `href` utilizzabile è cliccato una volta, mai ritentato e scritto in `steps.jsonl` prima di osservarne il risultato. Decide comunque il test dello stadio (`is_listing`, `is_product`, `is_cart`, pagina di checkout): una scelta sbagliata diventa una pagina `extra`. Al più una richiesta per ricerca e sei per profilo (listing, prodotto, carrello, aggiunta al carrello, variante, ingresso al checkout); nulla è chiesto se `max_pages` non lascia posto alla pagina a cui porterebbe. Consenso, overlay, sonda di ricerca, ripetizioni, percorso ospite e test di inganno non sono mai chiesti. Ogni ricerca è registrata in `PageRecord.probes["jev_fallback"]` (elemento scelto, probabilità, latenza, token, motivo) e sommata in `model_calls.crawler_fallback` della run, e uno stadio così trovato porta l'avviso «non riproducibile tra run diverse». L'osservazione di `snapshot.js` copre solo il viewport (al più 250 elementi): un link sotto la piega non è scelto da Jev.
- **Mai ritentare.** Ogni controllo inviato è ricordato dalla scheda e non è mai inviato di nuovo sullo stesso documento (URL, `performance.timeOrigin` ed etichetta; un'origine non leggibile vale come qualunque documento a quell'URL): un click senza effetto visibile non è ripetuto da un successivo passaggio di pulizia. Un click la cui `Browser.act()` è fallita dopo che può essere arrivato alla pagina non è mai più inviato su quell'URL (`uncertain_earlier`). Una `StalePage` significa che non è partito nulla: la pagina è ripulita dagli overlay tardivi, osservata di nuovo e l'azione è provata una volta (`stale_reobserved`); una seconda `StalePage` è un fallimento.
- **Test di inganno** (`deception.py`, per profilo, in contesti isolati freschi dello stesso profilo). Contesto 1: il banner di consenso su un nuovo atterraggio, la prima visita della PDP, il test di ricarico dei nagging. Contesto 2: la seconda visita della PDP a almeno 10 s dalla prima e fino a 2 altri prodotti dell'elenco. Sneak into basket, opzioni pre-selezionate e nagging tra le pagine usano il funnel già raccolto. Le pagine caricate qui non sono pagine del funnel (snapshot `<profilo>-deception-<n>`). Le visite ai prodotti avvengono nello stato di consenso in cui il funnel ha finito (accetta o rifiuta): il contesto 2, e il contesto 1 quando l'applicazione della politica del test di consenso non è finita in quella scelta, applicano la politica della run a un nuovo atterraggio su `settings.url` (un nuovo documento: nulla è inviato due volte), registrato in `consent_contexts`. Un test il cui contesto non si apre, o la cui visita incontra una sfida anti-bot, la pagina di errore o nessun audit, è non valutato e non vale mai un 0 pulito. Metodo e limiti dei singoli test: 4.7.
- **Assestamento di pagina:** evento `load`, rete quieta per 1,5 s, LCP quieto per 2 s, con tetto di 20 s (`settle_timeout_s`); il motivo (`quiet`, `timeout`, `load_only`) è registrato. Una richiesta non-documento la cui risposta è arrivata e che poi non riceve dati per 1,5 s è rilasciata in silenzio (un corpo che la pagina non legge: Chrome non ne riporta la fine) e il rilascio non conta come attività. Le richieste del documento precedente non trattengono mai l'assestamento (inviate prima della richiesta del documento corrente, da un altro loader del frame principale, o da un altro loader prima del commit). Una richiesta di cui non arrivano mai le intestazioni smette di trattenerlo dopo 5 s e lascia la nota `settle:`; le connessioni di lunga durata (EventSource, WebSocket) e il polling della pagina non lo trattengono mai.
- **Durata.** Il documento non ne dà una stima: nessuna durata è documentata in un artefatto committato. La fissano i parametri: per profilo, fino a `max_pages` caricamenti di pagina (12 per impostazione predefinita), ciascuno con un assestamento fino a `settle_timeout_s` (20 s); la rete emulata e la CPU rallentata del profilo mobile; i test di inganno, con una seconda visita al prodotto a almeno 10 s dalla prima; `repeats`, che ricarica le pagine. In Claude Code il tool è mandato in background e la skill interroga lo stato con `wait_run` finché `timed_out` è vero (e `get_run`). Ogni run registra `created_at` e `finished_at` (li mostra `list_runs`): la durata reale è nell'artefatto di ciascuna run. Un solo audit alla volta per processo: due audit condividerebbero CPU e rete e falserebbero i tempi l'uno dell'altro.

### 5.3 Journey con obiettivo e oracolo indipendente

- **Obiettivo** in linguaggio naturale. Il modello o l'host sceglie solo operazione e **indice di un elemento osservato** (CLICK, TYPE_TEXT, SELECT, SCROLL_UP, SCROLL_DOWN, WAIT, DONE, BLOCKED); non emette mai selettori né codice. I valori da digitare derivano dall'obiettivo o dalla pagina e non sono mai dati personali o di pagamento: si digitano solo in campi di ricerca, quantità e codice sconto, e il server rifiuta un testo che somiglia a un'email, a un numero di telefono, di carta o di conto, a un IBAN, a un codice fiscale, a una data, a un indirizzo o a un codice di sicurezza della carta. Un nome non si distingue da una parola di prodotto: la garanzia è la guardia dei campi, il controllo del testo è una rete in più.
- **Policy `typesafe` (Jev)**, predefinita quando il server ha `TYPESAFE_API_KEY` (`run_journey(policy="auto")`; da CLI è l'unica): il loop originale dell'agente, `Agent` con `model.choose`, una richiesta TypeSafe per decisione con la testata dell'operazione e quelle dei target; il codice del journey lo avvolge solo per la misura (segni di tempo, `steps.jsonl`, filtro della guardia, oracolo). I valori da digitare vengono dal modello di testo (`TEXT_MODEL_API_KEY`): senza chiave non si digita nulla e non si inventa alcun testo, un TYPE_TEXT scelto da Jev è rifiutato prima di ogni input e, se l'oracolo poi non passa, l'esito è non valutabile (`text_helper_unavailable`; `text_refused` se la guardia rifiuta il testo scritto): il percorso scelto dal pilota è stato negato, quindi l'insuccesso non è del sito. Quanto costa decidere è registrato a parte, nel record del journey, dopo ogni decisione e alla fine: `model_calls` (decisioni, chiamate di testo riuscite, chiamate di testo senza testo utilizzabile `text_failed`, decisioni che non hanno eseguito nulla, richieste TypeSafe senza decisione), `timing_ms` (decisione, testo comprese le chiamate fallite, sito, totale) e `usage` (token). Il tempo di decisione non è mai tempo del sito. Se Jev non riesce a prendere nemmeno una decisione (chiave rifiutata) non è stato eseguito nulla e la skill rifà il journey con l'host.
- **Policy `host`** (senza chiave TypeSafe, o su richiesta): decide l'host (Claude Code), senza chiavi a pagamento; il tempo di decisione è escluso dal tempo attribuibile al sito, perché i marcatori del tempo sono sul lato browser (`performance.now()`). Costo: un giro MCP e una deliberazione per passo; la latenza di decisione dell'host è registrata per passo in `decision_latency_ms` ed esclusa dal tempo attribuibile al sito, e le decisioni non sono riproducibili.
- **Una mutazione del browser non si ritenta mai.** Una pagina diventata obsoleta (`StalePage`) restituisce una nuova osservazione senza consumare un passo. Ogni passo è scritto in `steps.jsonl` **prima** di osservarne il risultato: una riga di esecuzione subito dopo l'input (`flags.unobserved`), poi, dopo l'assestamento, una riga di misura con lo stesso numero di passo; la lettura tiene l'ultima riga di ogni passo, così un processo fermato durante l'assestamento lascia un'azione eseguita e non misurata (mai un click senza effetto). Ogni osservazione porta un `observation_id` e `act` nomina quello su cui la scelta è stata fatta: una scelta su un altro (una chiamata ripetuta o parallela) non esegue nulla (`stale: true`) e l'indice deve ancora indicare lo stesso elemento dello stesso documento. Ogni osservazione passa dalla `CheckoutGuard` prima che una policy la veda, e l'azione la ripassa subito prima dell'input.
- **Oracolo indipendente a set chiuso**, valutato dopo il journey sullo stato reale della pagina: `cart_contains_item_under_price`, `cart_not_empty`, `pdp_reached`, `search_results_shown`. Uno stato DONE dell'agente non è una prova di successo. Se il journey è finito sul carrello l'oracolo lo legge dov'è (ricaricarlo rinvierebbe la richiesta che l'ha prodotto, per esempio un GET di aggiunta); altrimenti apre il carrello in una nuova scheda dello stesso contesto (stessi cookie), trovato senza l'agente: una pagina del journey classificata come carrello, un URL visitato che combacia con i pattern del lessico, o il link al carrello letto da `audit.js` sulla pagina finale (stesso dominio registrabile). Le chiavi di query che nominano un'azione sul carrello (`add-to-cart`, `remove_item`, `quantity`, `aggiungi`, ...) sono tolte prima del caricamento e un URL il cui percorso ne nomina una (`/cart/add`) non è mai caricato. Il prezzo di una riga è unitario o di riga secondo l'aritmetica del carrello stesso; se resta ambiguo vale la lettura maggiore, e se solo la minore farebbe passare l'oracolo l'esito è non valutabile (`cart_price_ambiguous`) e non un fallimento del sito. Un oracolo controlla solo ciò che i suoi parametri dicono: i cart oracle un prezzo o un carrello non vuoto, non le parole del prodotto; `pdp_reached` il tipo di pagina più le parole della query e il prezzo.
- **Metriche del journey** derivate dai passi (definizioni in 4.1 e 4.2.5): click morto, rage (stesso target almeno 3 volte consecutive senza cambiamento), backtrack, Lostness (solo se R è noto e il journey è riuscito), navigazioni inattese (compresi i record `NAVIGATION` tra due passi), CLS post-input, latenza di interazione, tempo di risposta. Gli overlay incontrati restano evidenza di pagina: `FAI.OVERLAY_*` sono KPI `checks`, non metriche del journey. I click del passo di consenso fatti prima della prima decisione (`source: "consent"`, 5.2) non sono azioni del pilota: nessuna di queste metriche li conta (né `FAI.ACTIONS_TO_GOAL`, né il tempo del sito, né i click morti), e nemmeno l'elenco dei passi che legge l'oracolo; il loro tempo resta solo nel tempo totale (`timing_ms.wall`). Con `consent: "none"` il banner resta tra gli elementi offerti e un suo click scelto dal pilota è un'azione come le altre.
- **Budget** di passi (`max_steps`, predefinito 40, al più 60) e stato finale: `done`, `blocked`, `stopped_at_checkout_boundary`, `budget_exhausted`, `error`, oppure `abandoned` (chiuso senza verifica perché nessuno lo guidava per 10 minuti, il server si è fermato o il processo è sparito: la run è `partial`, o `failed` se non ha pagine). Un audit interrotto da un segnale, da un arresto o da un processo morto è `failed` con un errore `interrupted: ...`: giudizi e punteggi lo rifiutano e va rieseguito.

### 5.4 Profili device

Versione `profiles.v1`.

| Parametro | `mobile` | `desktop` |
|---|---|---|
| Viewport | 390x844, DPR 3, mobile | 1366x768, DPR 1 |
| Rete | Latenza 150 ms, 1,6 Mbps in discesa (209.715 B/s), 750 Kbps in salita (96.000 B/s) | Nessun throttling |
| CPU | Rallentamento 4x | 1x |
| Touch | Sì | No |
| User agent | Chrome mobile (Android) senza "Headless" | Chrome desktop (Windows) senza "Headless" |

Il profilo mobile applica via CDP (`Emulation.setCPUThrottlingRate`, `Network.emulateNetworkConditions`) parametri vicini a quelli del preset mobile di Lighthouse (150 ms di RTT, 1,6 Mbps in discesa, 750 Kbps in salita, CPU 4x; i valori esatti del preset, e la differenza tra throttling simulato e di DevTools, non sono riverificati qui): li **approssima** e non replica Lighthouse, quindi i punteggi non sono confrontabili con quelli di Lighthouse o PageSpeed Insights. La versione di Chrome nello user agent segue quella del browser in esecuzione.

### 5.5 Igiene di misura

- **Contesto isolato per profilo** (`Target.createBrowserContext`, `disposeOnDetach`): un contesto per profilo per l'intero funnel, perché i cookie devono persistere per l'aggiunta al carrello. I test di inganno usano **due ulteriori contesti isolati** freschi con lo stesso profilo.
- **Cache fredda e navigazione per URL:** cache disabilitata, contesto nuovo. Ogni tempo di pagina è una navigazione a freddo per URL; la prerenderizzazione è disabilitata e i documenti caricati in anticipo dalle regole di speculazione del negozio non hanno tempi (nota di metodo in 4.1). La misura a cache calda è fase 2.
- **Browser visibile:** si usa headless `--headless=new`, che riporta `visibilityState = visible`, necessario per FCP e LCP. Un tab in background darebbe `hidden` e nessuna metrica di paint: in quel caso FCP e LCP sono "non valutabili" con motivo `background_tab`, mai 0. Il valore di `visibility_state_at_load` è registrato per ogni pagina.
- **Lingua e identità:** `Accept-Language` coerente con il locale (`it-IT` di default), user agent senza "Headless", emulazione del focus.
- **Lingua della pagina:** le parole di una pagina (lessico di `audit.js`, classificatore, controlli) si leggono nella lingua che dichiara in `<html lang>` quando il lessico la conosce, altrimenti in quella della run. Una lingua dichiarata diversa da quella della run cede a quest'ultima quando il testo visibile si legge in quella lingua (almeno 5 parole funzionali, il doppio di quelle della lingua dichiarata): un negozio italiano il cui tema dichiara `lang="en"` si legge in italiano, e il lessico italiano comprende quello inglese. La stessa lingua sceglie la formula di `CCL.READABILITY` (Gulpease o Flesch).
- **Trasporto CDP diretto** (websocket, coda di eventi fino a 200.000 voci) per la contabilità dei byte: il trasporto di `browser_harness` ha un buffer di 500 eventi, quindi perde eventi di rete. Con quel trasporto (`lossy`) i KPI di rete ripiegano su Resource Timing, marcati `source: resource_timing`, con copertura minore, e `PERF.TTFB`, sul profilo mobile, vale solo se l'evento del documento corrente è arrivato (sul desktop ripiega su Navigation Timing, `ttfb_source: navigation_timing`); `transferSize` di Resource Timing vale 0 per le risorse cross-origin senza Timing-Allow-Origin (comportamento generale dei browser, non riverificato dalla ricerca).
- **Artefatti:** per ogni pagina si salvano snapshot DOM e screenshot (opzionale) e i riepiloghi di rete e di errori nel `run.json`. La ricerca raccomanda di archiviare anche HAR e hash dei contenuti: sono raccomandazioni, non requisiti del v1.

### 5.6 Sicurezza e limiti di raccolta

Regole rigide, valide per audit e journey (`CheckoutGuard`):

- **Mai ordini.** Nessun click su etichette che pagano o confermano un ordine (lessico IT/EN, riconosciuto in ogni lingua: "Paga ora" è rifiutato anche in una run in inglese). Unica eccezione, un link dello stesso sito il cui `href` osservato è un URL di checkout, perché "Concludi ordine" è come il vecchio WooCommerce italiano dice "Procedi al checkout" e un GET verso lo stadio in cui la run si ferma non può fare un ordine; un pulsante con la stessa etichetta resta vietato. Nessun invio di form di checkout, nessuna creazione di account.
- **Mai dati personali nei campi.** La digitazione è vietata per impostazione predefinita: mai in campi password, di pagamento (`cc-*`, carta, CVV, IBAN), di dati personali (nome, email, telefono, indirizzo, codice fiscale, data di nascita) o di newsletter, né in un form con una password o un campo di pagamento, né in una pagina di checkout, né in una ricerca che chiede un'email o un telefono. Restano solo i campi di ricerca, i campi di quantità con etichetta e i campi del codice sconto. Un audit digita soltanto la parola della sonda di autocomplete.
- **Stop al primo step del checkout** e blocco degli host esterni. Una pagina è di checkout se lo dice il classificatore, se i campi osservati chiedono dati di pagamento (un campo CVV/CVC/CSC, o due parole di campo di pagamento sulle etichette dei campi e sul testo del loro form), o se il suo URL è un URL di checkout che non è anche di carrello né una pagina di contenuto; mai dal testo visibile nel viewport. Su una pagina di checkout restano permessi solo lo scorrimento, l'attesa e i link che portano altrove. Nulla si fa su una pagina di un altro dominio registrabile.
- **Limiti noti:** il lessico dei pulsanti vietati è italiano e inglese; l'aggiunta al carrello è l'unica modifica deliberata dei dati del negozio (modifica lo stato lato server, la sessione carrello), accettata e documentata. Gli altri click (scelta di una variante, controllo ospite o CTA di checkout del carrello, controlli di consenso e di overlay) sono navigazione o scelte di interfaccia, ma su alcune piattaforme un pulsante di checkout o ospite invia un modulo e apre una sessione di checkout lato negozio, e una scelta di consenso può impostare cookie. Il classificatore e la guardia possono non concordare su una pagina di metodi di pagamento in un URL di checkout: la guardia si ferma (il lato sicuro).

Limiti di raccolta: bot detection e CAPTCHA (pagina `challenge`, nessuna elusione); banner di consenso, che distorcono i byte e bloccano i click; shadow DOM aperti, che `audit.js` legge ma `snapshot.js` (da cui vengono i controlli cliccati) non attraversa, per cui un passo che richiede un controllo in uno shadow root si ferma con un motivo; shadow DOM chiusi e iframe cross-origin (recensioni, pagamenti), coperti solo in parte; una sola variante A/B; per i journey su siti protetti si può usare il Chrome reale dell'utente (`browser=harness`), con KPI di performance non valutabili se la pagina non era visibile. Il rapporto elenca già tra i "non valutabili" gli stadi esclusi con il loro motivo (per esempio `bot_challenge`), segnala l'avvertenza sul widget non letto accanto ai KPI giudicati letti come assenza (sezione 1.2), e il `run.json` conserva la scelta sul consenso e i conteggi di iframe e shadow DOM chiusi. Il rapporto non contiene ancora un elenco generale ed esplicito di questi limiti; li elencano il README e `docs/engagement-design.md`.

### 5.7 Ripetizioni e mediane

- Il numero di ripetizioni è `repeats` (da 1 a 5, predefinito 1): con più di una, la home, il listing e la pagina prodotto accettati sono ricaricati prima di ogni interazione (un ricarico che `max_pages` non consente è annotato) e i KPI di pagina riportano la mediana tra una pagina e i suoi ricarichi, con i valori singoli in `evidence.values`. Il rapporto non mostra ancora una dispersione e non etichetta una singola esecuzione. Il rallentamento della CPU in un container e le reti reali producono variabilità.
- Per i KPI di pagina, la mediana pesata sul funnel dà peso doppio a PDP e PLP; per ogni KPI il valore complessivo è quello del profilo peggiore (3.3, punto 5); il punteggio è calcolato per profilo e complessivo.
- I controlli che variano nel tempo (countdown, scorte) richiedono due visite in due contesti isolati freschi, a almeno 10 s l'una dall'altra.
- Le esecuzioni ripetute di journey si fanno con policy `typesafe` (Jev) da CLI; la policy `host` è per l'uso interattivo e per chi non ha una chiave TypeSafe. Né l'una né l'altra ha un seed: le mediane servono a questo.
- La ricerca raccomanda di ripetere ogni obiettivo e persona più volte, normalizzare con un percorso "d'oro" o con siti di riferimento (non in v1), e di non attribuire al sito un fallimento che può essere dell'agente.

### 5.8 Il rapporto

`report.html` è autocontenuto (CSS in linea, nessuna risorsa esterna, testo della pagina con escape) e in italiano; `report.json` porta gli stessi dati, con i codici al posto delle etichette. Contiene:

- il riquadro principale: ERS (o il motivo per cui non è pubblicato), Confidenza con copertura, quota inferita da LLM, rischio dark pattern, fattore limitante, riga "Ambito" e l'avvertenza che è una stima da sessioni sintetiche;
- i sei sotto-indici (peso, Confidenza, quota inferita, KPI limitanti) e il rischio dark pattern con i segnali valutati;
- il confronto per profilo: un punteggio per dispositivo. Il complessivo è un caso peggiore KPI per KPI e può stare sotto quello di ogni profilo (3.3, punto 5);
- i segnali di rischio, con severità, confidenza ed evidenza, e il journey (stato, pilota e modello, policy, oracolo, decisioni e loro tempo escluso da quello del sito, passi con operazioni e flag, in italiano);
- la tabella dei KPI, con i valori enum e label in italiano (per esempio `load_more` è mostrato come pulsante «carica altri»: `report.VALUE_LABELS`, fissato dai test alle mappe di `anchors.json`);
- le pagine analizzate (fase, tipo, URL, LCP, CLS, peso, e sotto la fase le note della raccolta come registrate): il peso è «—» quando nessuna richiesta è stata registrata, per esempio per una pagina letta sul posto e mai caricata; la fase `extra` è «nessuna (solo evidenza)»;
- l'elenco dei non valutabili con i motivi in italiano, gli avvisi e il metodo (formule, soglie, versioni, chi ha giudicato che cosa e con quali modelli, richieste TypeSafe del giudice e del fallback, limite dei giudici).

Una run di solo journey non ha osservazioni di audit: l'ERS non è pubblicato e i KPI dell'audit non osservati sono ripiegati in una riga («KPI dell'audit (N): nessuna osservazione in una run di solo journey; si valutano collegando la run a un audit»), che dice come collegarla: `score_run` dal server MCP, `jev-engage score --journey` dalla riga di comando. Convenzione dell'italiano rivolto all'utente: "run" è femminile (creata, completa, non riuscita); "audit" e "journey" sono maschili (concluso, bloccato, abbandonato).

---

## 6. Giudizi LLM

### 6.1 Principi

1. **Solo su snippet estratti**, mai su pagine intere: testo visibile verbatim, spazi collassati, al più 600 caratteri, di 13 tipi (`headline`, `cta`, `returns_policy`, `shipping_policy`, `scarcity`, `urgency`, `consent`, `modal_decline`, `checkbox_label`, `subscription`, `authority`, `testimonial`, `fee_line`). I tipi `shipping_policy`, `scarcity` e `urgency` sono estratti ma nessuna rubrica v1 li giudica (scarsità e urgenza sono controllate in modo deterministico).
2. **Etichette chiuse** con descrizioni (rubriche versionate `rubrics.v2`, ognuna con il suo giudice: `judge` vale `jev` o `claude`, 6.2). Presenza e assenza, non scale di severità: nello studio citato in 6.5 sulla severità l'accordo esatto è solo del 56%. Cambiare l'assegnazione del giudice o le etichette di una rubrica è un cambio di dato versionato e porta una nuova versione.
3. **Citazioni verbatim verificate:** ogni etichetta, salvo quelle esenti, richiede almeno una citazione che compaia nello snippet citato, dopo normalizzazione NFC e collasso degli spazi; il confronto non distingue maiuscole e minuscole e il testo conservato è sempre quello della pagina. Una citazione di meno di 8 caratteri è ammessa solo se è l'intero snippet. Le etichette esenti dalla citazione sono dichiarate per rubrica (`no_quote_labels`): `absent` in `returns_clarity`, `authority` e `social_proof`; `absent` e `unclear` nelle tre rubriche DPR; nessuna in `value_prop`, dove anche `unclear` richiede una citazione. Un verdetto con una citazione non verificabile, o senza citazione quando serve, è scartato. Jev non genera citazioni: sceglie l'id di uno snippet offerto e il codice ne copia il testo per intero (al più 600 caratteri), per cui la sua citazione è verbatim per costruzione e può essere più lunga di una frase.
4. **Campioni, maggioranza e passaggio a Claude.** I task di Claude (le rubriche `claude` e i task che Jev passa) hanno tre campioni e una maggioranza: pareggio o accordo sotto 2/3 danno un esito "incerto": etichetta nulla, KPI non valutato, mai 0. Un task che Jev accetta ha un campione, con accordo 1,0 e confidenza pari alla probabilità dell'etichetta; per un KPI DPR di confidenza (`0,8 x accordo`) un campione di Jev vale quindi quanto tre campioni concordi di Claude, e a sostituire i voti mancanti ci sono la soglia di probabilità e il controllo a ordine invertito. Jev passa a Claude un task se la probabilità dell'etichetta scelta è sotto 0,6 (`JEV_JUDGE_MIN_P`, tra 0,5 e 0,95), se la testata con le etichette in ordine inverso sceglie un'altra etichetta, se la testata di evidenza dell'etichetta scelta indica «nessuno snippet», o se la richiesta fallisce o la risposta non è valida.
5. **Cache** dei verdetti validati, con chiave `sha256` sull'id del modello, sul namespace del prompt del giudice (i verdetti dell'host e quelli di ciascun backend di ripiego non si mescolano; quelli di Jev hanno il namespace `jev:` più l'impronta della struttura della sua richiesta, per cui una modifica del testo delle regole o delle testate li rinnova senza interventi manuali) e su tutto ciò che il giudice vede (rubrica e versione, domanda, etichette, testi degli snippet, tipo di pagina, fase e lingua): una pagina invariata restituisce giudizi identici. La cache di Jev si legge solo se `TYPESAFE_MODEL` fissa una versione (per esempio `jev-1.13.0`), perché un alias come `jev-latest` cambia a ogni rilascio.
6. **Quota LLM.** Il rapporto mostra sempre la quota inferita da LLM (`llm_share`, sezione 6.4), che non distingue Jev da Claude. Gli id dei modelli dei giudici sono conservati nei verdetti e in `models` di ogni giudizio finale nel `run.json`; il rapporto li elenca nel piè di pagina ("Modelli dei valutatori") e `report.json` in `judgments.models`.
7. **Assenza letta senza LLM.** Per `returns_clarity`, `authority` e `social_proof`, una pagina raggiunta senza alcuno snippet pertinente vale come prova di assenza: riga deterministica (fuori dalla quota LLM) con etichetta `absent`, oppure `vague`, `weak` o `basic` se esiste un link ai resi, un badge o un rating senza testo. Per le altre rubriche, senza snippet il KPI resta non valutato (`no_snippets`).

### 6.2 I sette KPI giudicati

| Rubrica | KPI | Snippet | Etichette | Mappa v1 | Giudice |
|---|---|---|---|---|---|
| `returns_clarity` | `TRI.RETURNS_CLARITY` | `returns_policy` | `clear`, `vague`, `absent` | 100, 40, 0 | `jev` |
| `value_prop` | `CCL.VALUE_PROP_CLARITY` | `headline`, `cta` | `clear`, `partial`, `unclear` | 100, 50, 0 | `claude` |
| `authority` | `MPI.AUTHORITY` | `authority` | `present`, `weak`, `absent` | 100, 50, 0 | `jev` |
| `social_proof` | `MPI.SOCIAL_PROOF_RICH` | `testimonial` | `rich`, `basic`, `absent` | 100, 60, 0 | `jev` |
| `confirmshaming` | `DPR.CONFIRMSHAMING` | `modal_decline`, `consent` | `present`, `absent`, `unclear` | confidenza 0,8 x accordo; `absent` 0; `unclear` non valutato | `claude` |
| `trick_questions` | `DPR.TRICK_QUESTIONS` | `checkbox_label` | `present`, `absent`, `unclear` | come sopra | `jev` |
| `hidden_subscription` | `DPR.HIDDEN_SUBSCRIPTION` | `subscription`, `fee_line`, `cta` | `present`, `absent`, `unclear` | come sopra | `jev` |

**Perché questa assegnazione.** A Jev vanno le domande a etichetta chiusa e definite in modo operativo, che si risolvono sul testo: la finestra del reso più una condizione concreta, il nome di un ente o un numero di certificazione contro un'affermazione generica, la formulazione di un addebito ricorrente contro una CTA che presenta l'acquisto come gratuito o singolo, un elemento di autenticità accanto a un testo specifico, doppie negazioni e logica invertita. A Claude va ciò che chiede una lettura sfumata: la colpa, la vergogna o l'ironia di un'etichetta di rifiuto (`confirmshaming`) e la chiarezza percepita della proposta di valore (`value_prop`: «capisce subito che cosa si vende e perché sceglierlo»). La regola è nel codice e vale per tutte le rubriche: sotto la soglia di probabilità, con ordine inverso discorde o senza evidenza, Jev passa a Claude. Jev legge solo testo: un giudizio estetico o sulla prima impressione non gli è assegnabile.

Non si aggiungono ora rubriche di percezione nuove (pressione emotiva, rassicurazione, prima impressione): la pressione si sovrappone a `MPI.URGENCY_SIGNALS`, `MPI.SCARCITY_SIGNALS` e ai test DPR su timer e scorte, la rassicurazione ai segnali TRI, e un KPI nuovo richiede ancore, riga di catalogo e dati etichettati per tararle (sezione 3.6, una sola casa per segnale; il tono è già «evidenza debole e non è un KPI v1» in 2.7). La proposta per la fase 2 è nella sezione 8.

Ogni task riguarda una rubrica e una pagina, con al più 8 snippet; task con gli stessi snippet su più profili (per esempio mobile e desktop) sono condivisi, e `context.pages` elenca le pagine coperte. I quattro KPI giudicati a livello di sito (`TRI.RETURNS_CLARITY`, `CCL.VALUE_PROP_CLARITY`, `MPI.AUTHORITY`, `MPI.SOCIAL_PROOF_RICH`) si aggregano con `best`: vale la pagina più chiara; i tre DPR giudicati usano `max`. Tutte le mappe sono editoriali (`provisional`).

### 6.3 Esecuzione: Jev dal server, Claude dall'abbonamento tramite Claude Code

Il tool gira dentro **Claude Code come server MCP e plugin**. Il piano di architettura ha verificato che Claude Code non supporta il *sampling* MCP: il server non può chiedere a Claude di giudicare, e per la parte di Claude inverte la chiamata. La parte che Jev sa dare la giudica il server stesso, con una chiave TypeSafe che resta sul server. Senza chiave TypeSafe tutti i task vanno a Claude, come prima.

**Ramo Jev** (rubriche con `judge: jev`; tool MCP `judge_with_jev`, `jev-engage audit --judge jev`, `jev-engage judge --backend jev`). Una richiesta `systemone` per pagina, costruita come `model.choose` (stato più domande a scelta, `post_json`, `validate_choice`):

1. I task sono raggruppati per pagina. Lo stato porta, per ogni task, gli snippet con il loro tipo e testo; nessun URL, nessun id di run. Per ogni task la richiesta contiene una testata di etichetta (le etichette chiuse della rubrica con le loro descrizioni come criteri, la domanda della rubrica come istruzione), la stessa con le etichette in ordine inverso (Jev tende alla prima opzione) e, per ogni etichetta che richiede una citazione, una testata speculativa di evidenza («supponi che la risposta sia `clear`: quale snippet lo afferma alla lettera?») le cui opzioni sono gli id degli snippet offerti più «nessuno».
2. Il codice legge la testata di etichetta, poi quella inversa e solo la testata di evidenza dell'etichetta scelta (le altre non si leggono mai). Accetta il verdetto se la probabilità dell'etichetta è almeno 0,6 (`JEV_JUDGE_MIN_P`, tra 0,5 e 0,95), la testata inversa concorda e, se serve una citazione, la testata di evidenza ha scelto uno snippet. La citazione è il testo dello snippet scelto, per intero. Jev non scrive mai testo.
3. Altrimenti il task è **passato a Claude**: `task["escalation"]` registra il motivo (`low_probability`, `position_flip`, `no_evidence`; `request_failed` o `invalid_response` se non c'è una lettura di Jev, e una chiamata successiva li riprova), nessun verdetto è conservato e restano i tre campioni richiesti.
4. Un task accettato ha un campione (`samples_required` portato a 1) e `finalize` lo decide con accordo 1,0 e confidenza pari alla probabilità. Un fallimento di Jev non blocca mai l'audit.
5. Il tool non chiede nuove pagine dopo circa 30 s: `pages_left` conta le pagine che Jev non ha mai chiesto e si richiama finché `next` nomina `judge_with_jev`, cioè finché restano pagine e la chiamata ha ridotto `pages_left`. Finché TypeSafe risponde, ogni pagina costa una richiesta in tutto. Una pagina la cui richiesta è fallita conta come chiesta: i suoi task sono passati a Claude (`request_failed`) e una chiamata successiva la richiede, quindi con un fornitore che continua a fallire una pagina può costare più di una richiesta. Una chiamata che non chiede nessuna pagina mai chiesta (il tempo è andato a una pagina fallita) manda l'host a `get_judgment_tasks`: i task ancora aperti, comprese le pagine mai chieste, li giudica Claude. Jev giudica solo i task che nessun altro giudice ha toccato.

**Ramo Claude** (rubriche con `judge: claude` e task passati da Jev):

1. Il server prepara i **task di giudizio** dagli snippet dell'audit (tool `get_judgment_tasks`, a pagine di al più 15 task o 36.000 caratteri di testo dei task, per restare ben sotto il limite di output dei tool MCP). Ogni task dice il `judge` della sua rubrica, se è finale e, se Jev l'ha passato, perché (mai l'etichetta di Jev).
2. Claude Code, tramite la skill del plugin, lancia **tre subagent Sonnet** (`engagement-judge`) per ogni pagina che ha ancora un task aperto, il cui solo tool di dominio è la lettura dei task (`get_judgment_tasks`; `ToolSearch` serve soltanto a caricarlo), che restituiscono verdetti in JSON: etichetta, confidenza 0..1, citazioni `{snippet_id, quote}`, motivazione di al più 280 caratteri. Ogni giudice ha un `judge_id` (j1, j2, j3) e riporta l'id del modello. I task finali, decisi da Jev, sono saltati.
3. Il server **valida** i verdetti (`submit_judgments`): scarta etichette fuori dalla rubrica, citazioni non verbatim, confidenze fuori range, task già finalizzati.
4. `finalize_judgments`: maggioranza, pareggi incerti, cache. I giudizi diventano osservazioni `source: judged`, poi punteggi.

**Fallback fuori dal plugin:** `claude -p --output-format json --json-schema` senza `--bare` (usa il login dell'abbonamento), l'API Anthropic e un endpoint compatibile con OpenAI (`jev-engage judge --backend cli|api|openai`, o `jev-engage audit --judge ...`; la CLI di `claude` gira senza tool, senza il CLAUDE.md, gli hook e i plugin dell'utente e senza le chiavi API nell'ambiente, così usa il login dell'abbonamento). L'API serve solo come fallback. Dopo `audit --judge jev` i task rimasti aperti (rubriche di percezione e passaggi) si giudicano con `jev-engage judge RUN_ID --backend cli`, e poi `jev-engage score RUN_ID` aggiorna punteggi e rapporto: finché restano aperti non contano. Un `SamplingJudge` esiste dietro la stessa interfaccia e solleva `NotSupported` finché il client non dichiara il sampling.

### 6.4 Quota LLM dichiarata

Su 89 KPI, 7 sono giudicati (circa 8%). La quota LLM di un sotto-indice è il peso dei KPI giudicati valutati sul peso dei KPI valutati. Con i pesi di `anchors.json` v1 (da ricalcolare se i pesi cambiano), la quota massima teorica, a tutti i KPI valutati, è:

| Sotto-indice | Peso totale dei KPI | Peso dei KPI giudicati | Quota LLM massima v1 | Prior della ricerca |
|---|---|---|---|---|
| PERF | 19,5 | 0 | 0% | 0% |
| FAI | 34,0 | 0 | 0% | circa 5% |
| TRI | 18,5 | 2 (`TRI.RETURNS_CLARITY`) | circa 11% | circa 25% |
| PTI | 13,5 | 0 | 0% | circa 10% |
| CCL | 10,5 | 2 (`CCL.VALUE_PROP_CLARITY`) | circa 19% | circa 30% |
| MPI | 7,0 | 3,5 (`MPI.AUTHORITY`, `MPI.SOCIAL_PROOF_RICH`) | 50% | circa 35% |

MPI supera il prior della ricerca (50% contro circa 35%): è uno scostamento v1 noto, attenuato dal suo peso nell'ERS; gli altri sotto-indici restano sotto il prior (TRI 11% contro circa 25%, CCL 19% contro circa 30%, FAI e PTI 0% contro circa 5% e 10%). Con i pesi dei sotto-indici dell'ERS, la quota LLM complessiva massima è circa il 10%. I tre DPR giudicati non entrano nella quota dei sotto-indici (DPR non ha peso nei sotto-indici) ma sono marcati `source: judged` nei risk signals. Il valore reale dipende da quali KPI sono stati effettivamente valutati ed è riportato nel rapporto come `llm_share`. Un verdetto di Jev conta come giudizio LLM quanto uno di Claude: la quota non dipende dal modello.

### 6.5 Quanto sono affidabili i giudici LLM

| Evidenza | Risultato | Stato |
|---|---|---|
| GPT-4o con le euristiche di Nielsen (arXiv 2506.16345) | Trovò solo il 21,2% dei problemi trovati dagli esperti umani, più 27 problemi nuovi, con falsi positivi allucinati | [S] |
| Studio su oltre 850 valutazioni su 30 siti (candidato arXiv 2507.02306) | Kappa a coppie circa 0,50 (84% di accordo esatto) sul rilevamento dei problemi; sulla severità kappa pesato circa 0,63 ma solo 56% di accordo esatto | [S]; articolo non confermato |
| Duan et al., CHI 2024 | GPT-4 su mockup di UI in genere accurato sui design scadenti, peggiore sui design migliorati per iterazioni | [S] |
| AidUI (arXiv 2303.06782) | Precisione 0,66, richiamo 0,67, F1 0,65 su 10 tipi di dark pattern, da screenshot | [S] |
| Online-Mind2Web, WebJudge | Il giudice LLM concorda con gli umani circa all'85% delle volte: circa il 15% di disaccordo | come riportato nella ricerca |
| Preprint di giugno 2026 sulla riproducibilità | La temperatura 0 è necessaria ma non sufficiente: vicino al confine decisionale, a temperatura predefinita, gli item cambiavano fino a circa il 50% delle ripetizioni; anche con decodifica greedy forzata 1-2 item borderline su 7 restavano non riproducibili in 690 chiamate | [S] preprint |

Conseguenze nel catalogo: giudizi di presenza o assenza e non di severità; peso dei KPI giudicati limitato (al massimo il 50% in MPI e circa il 10% dell'ERS, sezione 6.4); quota LLM sempre calcolata e mostrata. I **tre giudici di Claude sono tre campioni dello stesso modello con lo stesso prompt**, non rater indipendenti: servono come controllo di stabilità. Il piè di pagina del rapporto lo dichiara accanto ai modelli usati: i valutatori "sono campioni dello stesso modello con lo stesso prompt: la maggioranza controlla la stabilità del giudizio, non un accordo tra valutatori indipendenti".

**Che cosa è un verdetto di Jev.** Un campione più un controllo di ordine e una soglia di probabilità, non un secondo parere: non esiste ancora, in un artefatto committato, alcuna misura della sua stabilità né del suo accordo con valutatori umani. I criteri sono le etichette delle rubriche, in italiano, inviate così come sono scritte; TypeSafe documenta che le lingue diverse dall'inglese funzionano "non ugualmente bene" e che Jev legge alla lettera, conta e calcola male e può essere influenzato dal testo della pagina (non è addestrato su contenuti avversari). Quanto spesso Jev passa a Claude, quanto sono stabili le sue etichette ripetendo la stessa richiesta e se una rubrica richieda criteri in inglese (una modifica versionata) lo mostra `scripts/smoke_engagement.py` (`--repeat N` per la stabilità, la probabilità media per rubrica, i passaggi per motivo); nessun numero è riportato qui finché il `smoke_summary.json` di un'esecuzione reale non è committato.

**Riproducibilità senza temperatura.** Su Claude Sonnet 5.5 un valore non predefinito di `temperature`, `top_p` o `top_k` restituisce HTTP 400 [V, guida alla migrazione]. La riproducibilità poggia quindi su cinque misure: etichette chiuse con output strutturato, citazioni verificate programmaticamente sullo snapshot, voto a maggioranza su più campioni, memoizzazione su hash del contenuto, versioni di rubrica e modello fissate. Gli output strutturati di Anthropic (`output_config.format` con schema JSON) non supportano vincoli numerici minimo e massimo né di lunghezza: le scale sono codificate come enum e l'intervallo è validato dopo [V]. Per Jev non c'è alcun parametro di temperatura né di seed: la riproducibilità poggia su etichette chiuse, soglia di probabilità, controllo a ordine invertito, evidenza scelta per id e cache su un modello fissato. TypeSafe dichiara risposte stabili, ma i suoi cookbook mostrano anche casi borderline che cambiano etichetta tra due richieste identiche: la stabilità su pagine reali va misurata (`--repeat` dello smoke), non data per scontata.

---

## 7. Validità e calibrazione

### 7.1 Che cosa dice la letteratura sugli agenti sintetici

| Opera | Risultato | Rilevanza per la validità |
|---|---|---|
| UXAgent (Lu et al., CHI EA 2025; arXiv 2504.09407, 2502.12561) | Generatore di persona, agente a doppio ciclo (veloce e lento), connettore browser; simula migliaia di utenti su uno shop | 5 ricercatori UX nel framework paper e 16 nella demo lo apprezzano ma sollevano riserve: serve a pre-testare il disegno di uno studio, non a sostituirlo |
| AgentA/B (arXiv 2504.09723) | 1.000 agenti su Amazon.com; una lista di filtri ridotta ha prodotto più acquisti e più azioni di filtro, nella stessa direzione di un esperimento umano parallelo | Gli agenti sono più diretti e fanno sequenze più corte di 1 milione di utenti umani: accordo direzionale, non assoluto |
| "Can LLM Agents Simulate Multi-Turn Human Behavior?" (ACL 2026) | 31.865 sessioni di shopping reali; gli LLM con prompt prevedono la prossima azione umana con accuratezza di circa l'11,86%; fine-tuning e ragionamento aiutano poco | Fedeltà debole a livello di azione |
| Online-Mind2Web (arXiv 2504.01382) | 300 compiti su 136 siti live; la maggior parte degli agenti ne completa circa il 30%; il giudice WebJudge concorda con gli umani circa all'85% | I progressi riportati erano troppo ottimistici; un giudice LLM aggiunge circa il 15% di disaccordo |
| WebArena (arXiv 2307.13854) | GPT-4 14,41% contro 78,24% umano su 812 compiti | Risultato del 2023: a limitare il successo è la capacità dell'agente, non la qualità del sito |
| Attacchi con pop-up (Zhang, Yu, Yang, ACL 2025; arXiv 2411.02391) | Pop-up avversariali cliccati dagli agenti l'86% delle volte; successo dei compiti −47%; l'istruzione di ignorarli non aiuta | Gli agenti reagiscono agli overlay in modo diverso dagli umani (rilevante per `FAI.OVERLAY_*`) |
| NN/g, utenti sintetici | Sycophancy, completamento irrealisticamente alto, intuizioni superficiali | Da usare solo per formulare ipotesi |
| "Lost in Simulation" (arXiv 2601.17087) | Utenti simulati conversazionali sono proxy inaffidabili; il successo dell'agente varia fino a 9 punti a seconda dell'LLM che interpreta l'utente | Direzione opposta (utenti simulati che valutano agenti), ma mostra che la scelta del simulatore sposta i risultati |
| Park et al. 2024 (arXiv 2411.10109) | Agenti costruiti da interviste replicano le risposte a sondaggio di 1.052 persone con accuratezza pari all'85% di quella con cui le persone replicano sé stesse a due settimane | Fedeltà attitudinale con dati ricchi; non valida il comportamento di navigazione |

### 7.2 Minacce alla validità e contromisure

Le minacce sono in gran parte una sintesi della ricerca (sezione C4 della ricerca di performance).

| Minaccia | Contromisura nel catalogo |
|---|---|
| L'agente legge DOM o albero di accessibilità, non la vista umana: contrasto, gerarchia visiva e prominenza non sono "visti" | Controlli geometrici espliciti (contrasto, dimensione dei target, posizione above the fold) calcolati sul DOM |
| Percorsi ottimistici: l'agente è diretto e non si distrae | Usare il risultato per confronti relativi; ancore di `FAI.ACTIONS_RATIO` editoriali |
| La latenza LLM gonfia il tempo sul compito e maschera la latenza del sito | Scomposizione del tempo: `FAI.TIME_ON_TASK_SITE` esclude la decisione |
| Il fallimento di un journey può dipendere dall'agente | Oracolo indipendente; ripetizioni; la ricerca propone siti di riferimento o un percorso "d'oro" per normalizzare (non in v1) |
| Non determinismo | `repeats`, mediane e dispersione; gli stadi trovati dal fallback di Jev segnalati come non riproducibili |
| Rumore dei giudici LLM (circa il 15% di disaccordo con gli umani) | Oracoli programmatici dove possibile; LLM solo su 7 KPI, con tre campioni di Claude oppure un campione di Jev con soglia di probabilità e controllo a ordine invertito, e quota dichiarata |
| Soddisfazione autodichiarata inaffidabile | SUS e SEQ compilati da un LLM non sono usati come metrica |
| Contenuti diversi serviti all'agente (bot detection, CAPTCHA, consent wall) | Registrati come eventi; pagina `challenge` non valutabile; scelta sul consenso registrata, anche quella che il journey fa sulla pagina iniziale prima della prima decisione del pilota |
| Nessun confronto con gli umani | Piano di calibrazione (7.4); finché non esiste, i numeri assoluti non vanno presi alla lettera |

### 7.3 PURE come tetto realistico di validità

PURE è un metodo comparabile di valutazione da esperti: ogni passo del compito riceve da 1 a 3, il punteggio del compito è la somma, un passo rosso colora l'intero compito. La sua validità pubblicata [V]: correlazione r di circa 0,5 con SEQ e SUS e di circa 0,6 con SUPR-Q, **nessuna correlazione significativa con tempo e completamento dei compiti**; cioè circa il 25-36% di varianza spiegata. È il **tetto realistico** per un punteggio strutturale da esperti, e quindi per l'ERS. I modelli di Reinecke spiegano circa metà della varianza dell'appeal percepito in valutazioni di laboratorio, non della conversione [V]. Non ci si aspettano correlazioni alte con le metriche reali.

### 7.4 Piano di calibrazione con GA4 e Microsoft Clarity

**Bersagli** (dati reali, per pagina di atterraggio o per template):

| Fonte | Metriche |
|---|---|
| Google Analytics 4 [V] | Tasso di engagement (sessione con engagement: oltre 10 s, oppure almeno un evento chiave, oppure almeno 2 pagine o schermate; tasso = sessioni con engagement / sessioni); bounce rate = 1 − tasso di engagement; `add_to_cart`; `begin_checkout` → `purchase` |
| Microsoft Clarity [V] | Percentuale di rage click, dead click, scroll eccessivo, quick back; errori JavaScript e di click |
| Contentsquare, se disponibile (vendor) | Frustration score |

I connettori GA4 e Clarity sono già disponibili in Claude Code.

**Metodo** (ricerca, sezione 5.3):

1. Unire le pagine o i siti sottoposti ad audit alle loro metriche comportamentali.
2. Calcolare la correlazione di Spearman ρ tra indice ed esito, con intervalli bootstrap.
3. Controllare per fonte di traffico, dispositivo, fascia di prezzo e stagionalità.
4. Tenere dati fuori campione e **preregistrare le ipotesi** (per esempio: FAI deve correlare negativamente con l'abbandono tra inizio checkout e acquisto).
5. Solo con abbastanza siti, regredire l'esito sui sotto-indici (ridge o logistica) per sostituire i pesi prior; altrimenti restano i prior.
6. **Evitare la circolarità:** non tarare le ancore sugli stessi dati usati per validare.
7. Riportare la deriva: qualità della calibrazione per ogni versione di rubriche e ancore.

**Ipotesi candidate da preregistrare.** Solo la prima riga viene dalla ricerca; le altre sono proposte del catalogo, non evidenza.

| Indice | Esito bersaglio | Segno atteso |
|---|---|---|
| FAI | `begin_checkout` → `purchase` (abbandono); Clarity: dead click, rage click, quick back | Correlazione negativa con l'abbandono e con la frustrazione |
| PERF | Tasso di engagement e bounce per pagina di atterraggio; errori JavaScript di Clarity | Correlazione positiva con l'engagement |
| PTI | Abbandono tra carrello e inizio checkout | Correlazione negativa |
| TRI | `begin_checkout` → `purchase` | Correlazione positiva |
| CCL, MPI | Tasso di `add_to_cart` sulle PDP | Correlazione positiva |
| DPR | Non validare come predittore di conversione: i dark pattern possono aumentare la conversione nel breve periodo (Luguri e Strahilevitz), ma sono un rischio di fiducia e normativo | Nessuna ipotesi di conversione; trattare come rischio |

---

## 8. Fase 2 (rimandato)

| Voce | Perché è rimandata | Fonte |
|---|---|---|
| axe-core completo | La v1 usa un sottoinsieme integrato (`FAI.A11Y_BASIC`). Con axe si conteggiano i problemi rilevati per impatto (critical, serious) con tag `wcag2a`, `wcag2aa`, `wcag21aa`, `wcag22aa`; i pesi di Lighthouse sono critical 10, serious 7, moderate 3, minor 1. L'automazione rileva circa il 57% dei problemi per volume (vendor). Licenza MPL-2.0 (copyleft a livello di file): da decidere se includere la libreria | Ricerca di performance, B3; piano di architettura |
| Speed Index | Richiede un filmstrip o una esecuzione Lighthouse separata. Bande mobile: buono ≤3,4 s, da migliorare ≤5,8 s | web.dev, docs Chrome |
| CrUX e INP reale | Dati di campo, riferimento per le priorità; la documentazione CrUX non è stata riverificata dalla ricerca | web.dev, lab e campo |
| Sottoparti dell'LCP | TTFB, load delay, load duration, render delay: ripartizione ideale circa 40%, meno del 10%, circa 40%, meno del 10% | web.dev, ottimizzare LCP |
| Checkout oltre il primo step | Numero di passi (media Baymard 5,1 nel 2024; prior della ricerca ≤3 = 100, 5 = 60, ≥8 = 0), validazione inline, messaggi di errore, metodi di pagamento, express pay, ricerca di indirizzo. Serve un negozio in modalità test o l'arresto prima di "conferma ordine" | Baymard; Mathur (crawler fermo al checkout) |
| Calibrazione con GA4 e Clarity | Sezione 7.4 | Ricerca di psicologia, 5.3 |
| Altre lingue | La v1 copre IT e EN. Il lessico va esteso a DE, FR, ES (per esempio "Warenkorb", "panier", "carrito") | Ricerca di performance, B2 |
| Cache calda e prestazioni a visita ripetuta | La v1 misura a cache fredda | Piano di architettura |
| Ricalibrazione delle severità DPR | Dopo il primo audit di correlazione o almeno 20 audit reali (soglia scelta dal progetto): severità graduate per danno, verso le fasce della ricerca (ipotesi in 3.4, anche per i tre segnali giudicati: 0,15, 0,35, 0,5), e chiave opzionale `severity` in `anchors.json` | Ricerca di psicologia, 2.3 |
| Copertura sul catalogo intero | Far contare contro la copertura le famiglie non eseguite: un audit statico con funnel completo leggerebbe 78% (B), con i giudizi 88% (A) (sezione 3.5) | Calcolo di progetto sul registro v1 |
| Percentili sul corpus, audit di correlazione, sensibilità e intervalli | Servono almeno 100 negozi: alfa di Cronbach, PCA, Monte Carlo su pesi (±20%) e ancore (±5%) | Ricerca di psicologia, 2.4 e 2.5 |
| Efficacia della ricerca | Zero risultati, riformulazioni per compito, latenza dell'autocomplete (NN/g: primo tentativo riuscito 51%, secondo 32%, terzo 18%) | NN/g |
| Stato dopo il pulsante Indietro | Filtri, pagina e scroll preservati dopo PDP→Indietro; Baymard: il 59% dei siti non rispetta le aspettative (anno non indicato nella ricerca) | Baymard |
| Errori di validazione e recupero | Richiede interazioni con i form | Ricerca di performance |
| Pulsante di recesso | Direttiva (UE) 2023/2673, dal 19 giugno 2026: ora verificabile per gli shop UE | [S] |
| Storico dei prezzi (Omnibus) | Verificare il prezzo di riferimento richiede crawl ripetuti nel tempo: è monitoraggio, non audit a visita singola | [S] |
| Altri test di inganno | Messaggi di attività o alta domanda, messaggi a tempo senza scadenza, pressured selling, difficoltà di disdetta (flusso autenticato) | Mathur 2019 [V] |
| Altre rubriche LLM | Tono (pressione contro informazione), coerenza, simpatia, unità. Candidata concreta: `CCL.EMOTIONAL_PRESSURE` (giudice `claude`, snippet `scarcity`, `urgency`, `cta`, etichette `informative`, `pressuring`, `absent`, casa CCL, peso 1, provvisoria), solo se un insieme etichettato mostra che si separa dai test DPR su timer e scorte (sezione 3.6) | Ricerca di psicologia; progetto Jev |
| Rilevamento della paginazione a scorrimento infinito | `FAI.PLP_PAGINATION` prevede il valore `infinite`, ma `audit.js` non lo rileva: oggi vale `none` | Codice v1 |
| Ancore Flesch per l'inglese | Bande proprie del Flesch [M] come secondo set di punti per lingua; `CCL.READABILITY` tornerebbe Pubbl. | Codice v1 |
| Secondo criterio di Mathur sui countdown | L'offerta ancora valida a scadenza non è verificata | Mathur 2019 [V] |
| Exit-intent e popup temporizzati | `DPR.NAGGING_OVERLAYS` rileva solo overlay che tornano dopo una chiusura | Brignull [V] |
| Elenco generale dei limiti nel rapporto | Oggi il rapporto riporta stadi non valutabili e l'avvertenza sui widget non letti; l'elenco completo è nel README | Codice v1 |
| Giudici via sampling MCP | `SamplingJudge` solleva `NotSupported` finché Claude Code non dichiara il sampling | Piano di architettura |
| Criteri in inglese per Jev | Se lo smoke mostra probabilità basse o molti passaggi a Claude su una rubrica, un blocco con criteri in inglese per quella rubrica (modifica di dato versionato: nuova versione delle rubriche) | Documentazione TypeSafe (lingue diverse dall'inglese); smoke |
| Evidenza di Jev a livello di frase | La citazione di Jev è oggi l'intero snippet scelto (fino a 600 caratteri): offrire a Jev frasi spezzate dal codice, con id, nella stessa testata | Codice v1 |
| Fallback di Jev oltre il viewport | L'osservazione copre solo il viewport: uno scorrimento limitato prima della richiesta richiederebbe una seconda richiesta per ricerca (da decidere) | Codice v1 |
| Tarare la soglia di Jev e misurarne l'accordo con gli umani | La soglia 0,6 è quella degli esempi di TypeSafe, non tarata su dati nostri; serve un insieme etichettato (sezione 7.4) e un'esecuzione reale dello smoke | Documentazione TypeSafe; smoke |

---

## 9. Bibliografia

Solo fonti presenti nei file di ricerca. I tag sono quelli dei file: [V] fonte primaria letta, [S] sintesi o fonte secondaria, [M] da memoria, da verificare; "vendor" indica studio di un'azienda interessata o commissionato da Google; l'assenza di tag significa che il file di ricerca non dichiara lo stato di verifica.

### 9.1 Performance e Core Web Vitals

- web.dev, Web Vitals e soglie, lette: https://web.dev/articles/vitals [V]
- LCP: https://web.dev/articles/lcp [V]; ottimizzare LCP: https://web.dev/articles/optimize-lcp [V]
- INP: https://web.dev/articles/inp [V]; ottimizzare INP: https://web.dev/articles/optimize-inp [V]
- CLS: https://web.dev/articles/cls [V]
- TTFB: https://web.dev/articles/ttfb [V]
- FCP: https://web.dev/articles/fcp [V]
- TBT: https://web.dev/articles/tbt [V]
- Dati di laboratorio e di campo: https://web.dev/articles/lab-and-field-data-differences
- Speed Index: https://developer.chrome.com/docs/lighthouse/performance/speed-index
- Long Animation Frames: https://developer.chrome.com/docs/web-platform/long-animation-frames
- Peso totale dei byte (Lighthouse): https://developer.chrome.com/docs/lighthouse/performance/total-byte-weight
- Punteggio di performance di Lighthouse: https://developer.chrome.com/docs/lighthouse/performance/performance-scoring [V]
- Throttling di Lighthouse: https://github.com/GoogleChrome/lighthouse/blob/main/docs/throttling.md
- Configurazione di Lighthouse (pesi di performance e accessibilità): https://raw.githubusercontent.com/GoogleChrome/lighthouse/main/core/config/default-config.js [V]; versioni su npm: https://registry.npmjs.org/lighthouse [V]
- Libreria web-vitals: https://github.com/GoogleChrome/web-vitals
- MDN, PerformanceObserver: https://developer.mozilla.org/en-US/docs/Web/API/PerformanceObserver/observe
- HTTP Archive Web Almanac 2025, peso delle pagine: https://almanac.httparchive.org/en/2025/page-weight
- HTTP Archive Web Almanac 2024, terze parti: https://almanac.httparchive.org/en/2024/third-parties
- NN/g, limiti dei tempi di risposta: https://www.nngroup.com/articles/response-times-3-important-limits/

### 9.2 Velocità, conversione ed engagement (studi vendor, osservazionali)

- Deloitte Digital e 55 per Google, "Milliseconds Make Millions": https://web.dev/case-studies/milliseconds-make-millions (vendor) [S]
- Google e SOASTA 2017, Think with Google (PDF letto in locale): https://www.thinkwithgoogle.com/_qs/documents/57/mobile-page-speed-new-industry-benchmarks.pdf (vendor) [V]
- Akamai, State of Online Retail Performance (primavera 2017): https://www.akamai.com/site/ja/documents/analyst-report/akamai-state-of-online-retail-performance-spring-2017.pdf (vendor); copertura stampa: https://www.mediapost.com/publications/article/306009/three-seconds-and-youre-out.html
- Portent (2022): https://www.portent.com/blog/analytics/research-site-speed-hurting-everyone.htm (vendor)
- Vodafone: https://web.dev/case-studies/vodafone (vendor)
- Rakuten 24: https://web.dev/case-studies/rakuten (vendor)
- Farfetch: https://web.dev/case-studies/farfetch (vendor)
- redBus: https://web.dev/case-studies/redbus-inp (vendor)

### 9.3 Baymard e NN/g su e-commerce

- Baymard, cause di abbandono del carrello: https://baymard.com/lists/cart-abandonment-rate [V]
- Baymard, campi del checkout: https://baymard.com/blog/checkout-flow-average-form-fields [V]
- Baymard, usabilità del checkout (benchmark 2025): https://baymard.com/blog/ecommerce-checkout-usability
- Baymard, stato dell'UX delle pagine prodotto: https://baymard.com/blog/current-state-ecommerce-product-page-ux
- Baymard, liste prodotto: https://baymard.com/blog/ecommerce-product-lists-report-and-benchmark
- Baymard, ricerca: https://baymard.com/blog/ecommerce-search-report-and-benchmark
- Baymard, UX benchmark e metodologia: https://baymard.com/ux-benchmark [V], https://baymard.com/research/methodology [V]
- Baymard, sigilli di fiducia: https://baymard.com/research-articles/site-seal-trust [S]; CXL: https://cxl.com/research-study/trust-seals/ [S]
- eMarketer, costi extra come primo motivo di abbandono: https://www.emarketer.com/content/extra-costs-are-the-top-reason-consumers-abandon-online-carts [S]
- NN/g, pagine prodotto: https://www.nngroup.com/articles/ecommerce-product-pages/
- NN/g, ricerca visibile e semplice: https://www.nngroup.com/articles/search-visible-and-simple/
- NN/g, breadcrumb: https://www.nngroup.com/articles/breadcrumbs/
- NN/g, scroll e attenzione (cifre da sintesi): https://www.nngroup.com/articles/scrolling-and-attention/ [S]
- NN/g, modali e non modali: https://www.nngroup.com/articles/modal-nonmodal-dialog/
- NN/g, F-pattern: https://www.nngroup.com/articles/f-shaped-pattern-reading-web-content-discovered/ [V]; schemi di scansione del testo: https://nngroup.com/articles/text-scanning-patterns-eyetracking/ [S]
- NN/g, utenti sintetici: https://www.nngroup.com/articles/synthetic-users/
- Google, dati strutturati Product: https://developers.google.com/search/docs/appearance/structured-data/product-snippet; merchant listing: https://developers.google.com/search/docs/appearance/structured-data/merchant-listing [V]
- Google, interstitial invasivi: https://developers.google.com/search/docs/appearance/avoid-intrusive-interstitials

### 9.4 Accessibilità

- W3C, WCAG 2.2 SC 2.5.8 (dimensione minima del target): https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html [V]
- web.dev, target di tocco accessibili: https://web.dev/articles/accessible-tap-targets
- WebAIM Million: https://webaim.org/projects/million/
- Deque, 57% dei problemi rilevabili in automatico: https://www.deque.com/blog/automated-testing-study-identifies-57-percent-of-digital-accessibility-issues/ (vendor)
- European Accessibility Act, sintesi per l'e-commerce: https://resignal.com/blog/the-eu-accessibility-act-what-ecommerce-websites-need-to-know-and-do/ [S]

### 9.5 Frizione, journey e metriche di settore

- MeasuringU, task completion: https://measuringu.com/task-completion/
- UIE, studio Porter sulla regola dei 3 click: https://articles.centercentre.com/?p=17
- MeasuringU, Lostness: https://measuringu.com/lostness/; Smith 1996 (articolo non aperto): https://academic.oup.com/iwc/article/8/4/365/686010
- Microsoft Clarity, metriche semantiche: https://learn.microsoft.com/en-us/clarity/insights/semantic-metrics [V]; rage click: https://clarity.microsoft.com/blog/rage-clicks-user-behavior/
- Contentsquare, rage click (definizione da sintesi): https://contentsquare.com/blog/rage-clicks-what-are-they-and-how-to-avoid-them/ [S]; frustration score: https://contentsquare.com/platform/capabilities/frustration-score/ (vendor); benchmark: https://contentsquare.com/guides/digital-experience-benchmark/frustration/ [S]; zone: https://contentsquare.com/guides/heatmaps/zone-based-heatmaps/ [S]
- Google HEART (Rodden, Hutchinson, Fu, CHI 2010): https://research.google.com/pubs/archive/36299.pdf; sintesi: https://www.productcompass.pm/p/the-google-heart-framework [S], https://ixd.prattsi.org/2018/12/googles-heart-framework-measuring-and-tracking-progress-towards-key-goals/ [S]
- MeasuringU, SUS: https://measuringu.com/sus/; SEQ: https://measuringu.com/seq10/; SUPR-Q: https://measuringu.com/suprq/ [V]
- MeasuringU, PURE: https://measuringu.com/pure/ [V]; articolo CHI: https://measuringu.com/wp-content/uploads/2016/05/CHI-pure2016.pdf [V]
- Nielsen, 10 euristiche: https://www.nngroup.com/articles/ten-usability-heuristics/ [V]; severità: https://www.nngroup.com/articles/how-to-rate-the-severity-of-usability-problems/ [V]
- Google Analytics 4, sessioni con engagement: https://support.google.com/analytics/answer/12195621 [V]

### 9.6 Modelli comportamentali, scelta, fiducia, estetica

- Fogg Behavior Model: https://www.behaviormodel.org/ [V]; abilità: https://www.behaviormodel.org/ability [V]; Stanford: https://behaviordesign.stanford.edu/resources/fogg-behavior-model
- Cialdini, sette principi: https://www.influenceatwork.com/7-principles-of-persuasion/ [V]
- Stanford Web Credibility Guidelines: https://credibility.stanford.edu/guidelines/index.html [V]; pubblicazioni (teoria prominence-interpretation): https://credibility.stanford.edu/publications.html [S]
- Legge di Hick: https://lawsofux.com/hicks-law/ [V]; Miller: https://lawsofux.com/millers-law/ [V]; effetto estetica-usabilità: https://lawsofux.com/aesthetic-usability-effect/
- Iyengar e Lepper (2000): https://faculty.washington.edu/jdb/345/345%20Articles/Iyengar%20%26%20Lepper%20(2000).pdf [V]
- Scheibehenne, Greifeneder, Todd (2010): https://ideas.repec.org/a/oup/jconrs/v37y2010i3p409-425.html [S]
- Chernev, Böckenholt, Goodman (2015): https://www.kellogg.northwestern.edu/academics-research/research/detail/2015/when-product-assortment-leads-to-choice-overload-a-conceptual/ [S]; rassegna del 2024: https://pmc.ncbi.nlm.nih.gov/articles/PMC11111947/ [S]
- Pirolli e Card, information foraging: https://en.wikipedia.org/wiki/Information_foraging [S]
- Spiegel Research Center, effetto delle recensioni: https://spiegel.medill.northwestern.edu/How-Online-Reviews-Influence-Sales [S]
- Barton et al. 2022, meta-analisi sulla scarsità: https://ideas.repec.Org/a/eee/jouret/v98y2022i4p741-758.html [S]
- Reinecke et al., CHI 2013: https://kgajos.seas.harvard.edu/papers/reinecke13aesthetics.pdf [V]; Reinecke e Gajos 2014: https://iis.seas.harvard.edu/papers/reinecke14visual.pdf [S]
- Lindgaard et al. 2006: https://www.websiteoptimization.com/speed/tweak/blink/ [S]; Tractinsky et al. 2000: https://academic.oup.com/iwc/article/13/2/127/898608 [S]
- Gulpease: https://www.preprints.org/manuscript/201811.0505 [S], https://www.w3.org/WAI/RD/2012/easy-to-read/paper3 [S]

### 9.7 Dark pattern e normativa

- Mathur et al. 2019: https://arxiv.org/abs/1907.07032 [V]; https://doi.org/10.1145/3359183
- Luguri e Strahilevitz: https://academic.oup.com/jla/article/13/1/43/6180579 [V]
- Brignull, deceptive.design: https://www.deceptive.design/types [V]; LLM e esperti sui dark pattern: https://deceptive.design/articles/ux-experts-vs-ai-exploring-the-performance-of-large-language-models-and-humans-on-detecting-dark-patterns [S]
- FTC, "Bringing Dark Patterns to Light": https://www.ftc.gov/reports/bringing-dark-patterns-light [V per l'esistenza]
- Commissione UE, sweep CPC (30 gennaio 2023): https://ec.europa.eu/commission/presscorner/api/files/document/print/en/ip_23_418/IP_23_418_EN.pdf [S]
- DSA art. 25: https://www.springlex.eu/en/packages/dsa/dsa-regulation/article-25/ [S]
- Pulsante di recesso, direttiva (UE) 2023/2673: https://www.hoganlovells.com/en/publications/eu-consumer-protection-law-update-new-mandatory-withdrawal-button-what-online-traders-need-to-know [S]; https://www.iubenda.com/en/blog/online-withdrawal-function-eu-directive-2023-2673/
- Omnibus e riduzioni di prezzo: https://sellforte.com/blog/omnibus-directive-how-does-it-impact-your-business [S]; https://www.rpclegal.com/snapshots/consumer/spring-2022/european-commission-publishes-guidance-on-price-promotions-under-the-omnibus-directive/ [S]; CGUE C-330/23: https://pagecrawl.io/blog/eu-omnibus-30-day-lowest-price-monitoring [S]
- Digital Fairness Act: https://electronlibre.info/articles/103953-digital-fairness-act-the-commission-wants-to-open-up-the-black-box-of-platform-design/ [S]
- Banner dei cookie: EDPB, task force: https://www.cnil.fr/en/edpb-adopts-final-report-outcome-cookie-banner-task-force [S]; Nouwens et al.: https://arxiv.org/abs/2001.02479v1 [S]
- AidUI: https://arxiv.org/abs/2303.06782v1 [S]

### 9.8 Indici compositi e giudici LLM

- OECD e JRC, handbook sugli indicatori compositi: https://knowledge4policy.ec.europa.eu/sites/default/files/jrc47008_handbook_final.pdf [S]; https://www.oecd.org/en/publications/handbook-on-constructing-composite-indicators-methodology-and-user-guide_9789264043466-en.html [S]
- Anthropic, guida alla migrazione a Sonnet 5.5: https://platform.claude.com/docs/en/models/sonnet-5-5/migration-guide [V]; output strutturati: https://platform.claude.com/docs/en/build-with-claude/structured-outputs [V]
- Riproducibilità a temperatura 0 (preprint, giugno 2026): https://huggingface.co/papers/2606.26185.md [S]
- LLM e valutazione euristica: https://arxiv.org/abs/2506.16345v1 [S]; candidato: https://arxiv.org/pdf/2507.02306 [S]; Duan et al., CHI 2024: https://people.eecs.berkeley.edu/~bjoern/papers/duan-heuristic-chi2024.pdf [S]

### 9.9 Agenti come utenti sintetici

- UXAgent: https://arxiv.org/abs/2504.09407; https://arxiv.org/pdf/2502.12561; https://arxiv.org/pdf/2504.09407 [S]
- AgentA/B: https://arxiv.org/abs/2504.09723; https://dl.acm.org/doi/10.1145/3772363.3799039
- "Can LLM Agents Simulate Multi-Turn Human Behavior?" (ACL 2026): https://aclanthology.org/2026.acl-long.2034.pdf
- Online-Mind2Web: https://arxiv.org/abs/2504.01382
- WebArena: https://arxiv.org/abs/2307.13854
- Attacchi con pop-up (Zhang, Yu, Yang, ACL 2025): https://arxiv.org/pdf/2411.02391
- "Lost in Simulation": https://arxiv.org/abs/2601.17087
- Park et al. 2024: https://arxiv.org/abs/2411.10109
