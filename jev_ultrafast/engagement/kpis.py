"""KPI registry: identity, owner sub-index, unit, producer and aggregation of every measured signal.

One home per signal: each KPI feeds exactly one sub-index (or the DPR risk penalty). Normalisation anchors and
weights are versioned data in anchors.json; tests assert both files describe the same KPI ids.
"""

from dataclasses import dataclass
from typing import Literal

Owner = Literal["PERF", "FAI", "TRI", "PTI", "CCL", "MPI", "DPR"]
Producer = Literal["checks", "friction", "judgments", "deception"]
Aggregate = Literal["wmedian", "median", "max", "min", "mean", "sum", "any", "all", "first"]
Scope = Literal["page", "site", "journey"]
ValueType = Literal["number", "bool", "enum", "confidence"]


@dataclass(frozen=True)
class Kpi:
    id: str
    owner: Owner
    unit: str
    producer: Producer
    scope: Scope
    aggregate: Aggregate
    value_type: ValueType
    description: str  # Italian, one line, used in the report
    judged: bool = False
    severity: float | None = None  # DPR only: severity of a confirmed signal (0..1)
    credit_unless: str | None = None  # MPI only: not credited when this DPR signal fired
    stages: tuple[str, ...] | None = None  # stages where the KPI is meaningful (None = any)


def _k(*args, **kwargs) -> Kpi:
    return Kpi(*args, **kwargs)


_PDP = ("pdp",)
_PLP = ("plp",)
_CART = ("cart",)
_CHECKOUT = ("checkout_entry",)
_FUNNEL = ("pdp", "cart", "checkout_entry")

KPI_LIST: tuple[Kpi, ...] = (
    # ------------------------------------------------------------ PERF: performance and robustness
    _k("PERF.TTFB", "PERF", "ms", "checks", "page", "wmedian", "number", "Time to First Byte"),
    _k("PERF.FCP", "PERF", "ms", "checks", "page", "wmedian", "number", "First Contentful Paint"),
    _k("PERF.LCP", "PERF", "ms", "checks", "page", "wmedian", "number", "Largest Contentful Paint"),
    _k("PERF.CLS", "PERF", "score", "checks", "page", "wmedian", "number", "Cumulative Layout Shift"),
    _k("PERF.CLS_POST_INPUT", "PERF", "score", "friction", "journey", "max", "number",
       "Spostamenti di layout subito dopo le azioni dell'agente"),
    _k("PERF.TBT_APPROX", "PERF", "ms", "checks", "page", "wmedian", "number",
       "Total Blocking Time approssimato (long task oltre 50 ms)"),
    _k("PERF.LOAF_COUNT", "PERF", "count", "checks", "page", "wmedian", "number", "Long animation frames"),
    _k("PERF.INP_SYNTH", "PERF", "ms", "friction", "journey", "max", "number",
       "Latenza di interazione dei click dell'agente (non confrontabile con INP reale)"),
    _k("PERF.BYTES_TOTAL", "PERF", "KB", "checks", "page", "wmedian", "number", "Peso trasferito della pagina"),
    _k("PERF.BYTES_JS", "PERF", "KB", "checks", "page", "wmedian", "number", "JavaScript trasferito"),
    _k("PERF.REQUESTS", "PERF", "count", "checks", "page", "wmedian", "number", "Numero di richieste"),
    _k("PERF.THIRD_PARTY_SHARE", "PERF", "%", "checks", "page", "wmedian", "number",
       "Quota di byte da domini di terze parti"),
    _k("PERF.IMG_OVERSIZED", "PERF", "count", "checks", "page", "wmedian", "number",
       "Immagini sovradimensionate (naturale >= 2x visualizzata)"),
    _k("PERF.CONSOLE_ERRORS", "PERF", "count", "checks", "page", "sum", "number", "Errori JavaScript e console"),
    _k("PERF.HTTP_ERRORS", "PERF", "count", "checks", "page", "sum", "number", "Risposte HTTP 4xx/5xx"),
    _k("PERF.ACTION_RESPONSE_MS", "PERF", "ms", "friction", "journey", "median", "number",
       "Tempo dalla azione alla prima risposta visibile"),
    # ------------------------------------------------------------ FAI: friction / ability
    _k("FAI.SEARCH_VISIBLE", "FAI", "bool", "checks", "site", "any", "bool",
       "Campo di ricerca visibile above the fold", stages=("home",)),
    _k("FAI.SEARCH_WIDTH", "FAI", "px", "checks", "site", "max", "number", "Larghezza del campo di ricerca",
       stages=("home",)),
    _k("FAI.SEARCH_AUTOCOMPLETE", "FAI", "bool", "checks", "site", "any", "bool", "Suggerimenti di ricerca",
       stages=("home",)),
    _k("FAI.PLP_FILTERS", "FAI", "count", "checks", "page", "max", "number", "Controlli di filtro nella categoria",
       stages=_PLP),
    _k("FAI.PLP_SORT", "FAI", "bool", "checks", "page", "any", "bool", "Ordinamento nella categoria", stages=_PLP),
    _k("FAI.PLP_RESULT_COUNT", "FAI", "bool", "checks", "page", "any", "bool", "Numero di risultati mostrato",
       stages=_PLP),
    _k("FAI.PLP_PAGINATION", "FAI", "enum", "checks", "page", "first", "enum",
       "Paginazione: pagination | load_more | infinite | none", stages=_PLP),
    _k("FAI.BREADCRUMBS", "FAI", "bool", "checks", "page", "any", "bool", "Breadcrumb su categoria/prodotto",
       stages=("plp", "pdp")),
    _k("FAI.PDP_CTA_ABOVE_FOLD", "FAI", "bool", "checks", "page", "all", "bool",
       "CTA aggiungi al carrello above the fold", stages=_PDP),
    _k("FAI.PDP_VARIANT_SELECTOR", "FAI", "enum", "checks", "page", "first", "enum",
       "Selettore varianti: buttons | select | none", stages=_PDP),
    _k("FAI.CART_EDITABLE", "FAI", "bool", "checks", "page", "any", "bool", "Quantita/rimozione modificabili",
       stages=_CART),
    _k("FAI.CHECKOUT_FIELDS", "FAI", "count", "checks", "page", "max", "number",
       "Campi visibili al primo step del checkout", stages=_CHECKOUT),
    _k("FAI.GUEST_CHECKOUT", "FAI", "bool", "checks", "page", "any", "bool", "Checkout come ospite disponibile",
       stages=("cart", "checkout_entry")),
    _k("FAI.FORCED_ACCOUNT", "FAI", "bool", "checks", "page", "any", "bool",
       "Registrazione obbligatoria prima del pagamento (negativo)", stages=("cart", "checkout_entry")),
    _k("FAI.AUTOCOMPLETE_ATTRS", "FAI", "%", "checks", "page", "min", "number",
       "Quota di campi con attributo autocomplete", stages=_CHECKOUT),
    _k("FAI.TARGET_SIZE_24", "FAI", "%", "checks", "page", "wmedian", "number", "Target interattivi >= 24 px"),
    _k("FAI.TARGET_SIZE_44", "FAI", "%", "checks", "page", "wmedian", "number", "Target interattivi >= 44 px"),
    _k("FAI.A11Y_BASIC", "FAI", "count", "checks", "page", "wmedian", "number",
       "Problemi base di accessibilita (alt, label, lang)"),
    _k("FAI.OVERLAY_INTERRUPTIONS", "FAI", "count", "checks", "page", "max", "number",
       "Overlay/popup che interrompono la pagina"),
    _k("FAI.OVERLAY_COVERAGE", "FAI", "%", "checks", "page", "max", "number",
       "Copertura massima del viewport da overlay"),
    _k("FAI.JOURNEY_SUCCESS", "FAI", "bool", "friction", "journey", "all", "bool",
       "Obiettivo raggiunto secondo l'oracolo indipendente"),
    _k("FAI.ACTIONS_TO_GOAL", "FAI", "count", "friction", "journey", "first", "number",
       "Azioni eseguite per arrivare all'obiettivo (informativo)"),
    _k("FAI.ACTIONS_RATIO", "FAI", "ratio", "friction", "journey", "first", "number",
       "Azioni eseguite / percorso minimo"),
    _k("FAI.TIME_ON_TASK_SITE", "FAI", "ms", "friction", "journey", "first", "number",
       "Tempo attribuibile al sito (esclusa la decisione)"),
    _k("FAI.DEAD_CLICK_RATE", "FAI", "%", "friction", "journey", "first", "number", "Click senza alcun effetto"),
    _k("FAI.RAGE_EVENTS", "FAI", "count", "friction", "journey", "first", "number",
       "Stesso target >= 3 volte senza cambiamento"),
    _k("FAI.BACKTRACK_RATE", "FAI", "%", "friction", "journey", "first", "number", "Ritorni a pagine gia viste"),
    _k("FAI.LOSTNESS", "FAI", "0-1", "friction", "journey", "first", "number", "Lostness (Smith 1996)"),
    _k("FAI.UNEXPECTED_NAV", "FAI", "count", "friction", "journey", "first", "number",
       "Navigazioni o tab inattesi"),
    # ------------------------------------------------------------ TRI: trust and risk reduction
    _k("TRI.HTTPS", "TRI", "bool", "checks", "page", "all", "bool", "HTTPS su tutto il percorso"),
    _k("TRI.MIXED_CONTENT", "TRI", "count", "checks", "page", "sum", "number", "Risorse http su pagina https"),
    _k("TRI.CONTACT_INFO", "TRI", "bool", "checks", "site", "any", "bool", "Contatti (email/telefono/indirizzo)"),
    _k("TRI.LEGAL_ID", "TRI", "bool", "checks", "site", "any", "bool", "Identificativo legale (P.IVA/VAT)"),
    _k("TRI.POLICY_LINKS", "TRI", "count", "checks", "site", "max", "number",
       "Link a resi, spedizioni, privacy, termini (0-4)"),
    _k("TRI.JSONLD_RETURN_POLICY", "TRI", "bool", "checks", "site", "any", "bool",
       "hasMerchantReturnPolicy nei dati strutturati", stages=_PDP),
    _k("TRI.JSONLD_SHIPPING", "TRI", "bool", "checks", "site", "any", "bool",
       "shippingDetails nei dati strutturati", stages=_PDP),
    _k("TRI.PAYMENT_LOGOS", "TRI", "count", "checks", "site", "max", "number", "Metodi di pagamento riconoscibili"),
    _k("TRI.REVIEWS_PRESENT", "TRI", "bool", "checks", "page", "any", "bool", "Recensioni sul prodotto",
       stages=_PDP),
    _k("TRI.REVIEW_COUNT", "TRI", "count", "checks", "page", "max", "number", "Numero di recensioni",
       stages=_PDP),
    _k("TRI.RATING_BAND", "TRI", "enum", "checks", "page", "first", "enum",
       "Fascia di rating: 4.0-4.7 | 4.8-5.0 | 3.5-3.9 | <3.5", stages=_PDP),
    _k("TRI.DELIVERY_TIME_STATED", "TRI", "bool", "checks", "page", "any", "bool",
       "Tempi di consegna indicati prima del checkout", stages=("pdp", "cart")),
    _k("TRI.RETURNS_CLARITY", "TRI", "label", "judgments", "site", "first", "enum",
       "Chiarezza della politica di reso: clear | vague | absent", judged=True),
    # ------------------------------------------------------------ PTI: price and cost transparency
    _k("PTI.PRICE_VISIBLE_PDP", "PTI", "bool", "checks", "page", "all", "bool", "Prezzo visibile above the fold",
       stages=_PDP),
    _k("PTI.PRICE_JSONLD_MATCH", "PTI", "bool", "checks", "page", "all", "bool",
       "Prezzo strutturato coerente con quello visibile", stages=_PDP),
    _k("PTI.SHIPPING_COST_PRE_CHECKOUT", "PTI", "bool", "checks", "site", "any", "bool",
       "Costo di spedizione visibile prima del checkout", stages=("pdp", "cart")),
    _k("PTI.FREE_SHIPPING_THRESHOLD", "PTI", "bool", "checks", "site", "any", "bool",
       "Soglia di spedizione gratuita comunicata"),
    _k("PTI.VAT_STATED", "PTI", "bool", "checks", "site", "any", "bool", "IVA inclusa/esclusa dichiarata",
       stages=("pdp", "cart", "checkout_entry")),
    _k("PTI.FUNNEL_PRICE_DELTA", "PTI", "%", "checks", "site", "max", "number",
       "Aumento non spiegato del totale PDP -> carrello -> checkout", stages=_FUNNEL),
    _k("PTI.STRIKETHROUGH_LOWEST30", "PTI", "bool", "checks", "page", "all", "bool",
       "Prezzo barrato accompagnato dal prezzo piu basso degli ultimi 30 giorni (Omnibus)"),
    _k("PTI.UNEXPLAINED_FEES", "PTI", "count", "checks", "site", "max", "number",
       "Voci di costo nel carrello/checkout non annunciate prima", stages=("cart", "checkout_entry")),
    # ------------------------------------------------------------ CCL: clarity and cognitive load
    _k("CCL.CTA_SALIENCE", "CCL", "score", "checks", "page", "wmedian", "number",
       "Salienza della CTA primaria (contrasto, area, unicita)", stages=("pdp", "cart")),
    _k("CCL.PRIMARY_CTA_COUNT", "CCL", "count", "checks", "page", "wmedian", "number",
       "CTA primarie in competizione above the fold"),
    _k("CCL.CHOICE_SUPPORT", "CCL", "bool", "checks", "page", "all", "bool",
       "Assortimenti ampi accompagnati da filtri o ordinamento", stages=_PLP),
    _k("CCL.INFO_SCENT_GENERIC", "CCL", "%", "checks", "site", "max", "number",
       "Link di navigazione con etichette generiche"),
    _k("CCL.READABILITY", "CCL", "score", "checks", "page", "wmedian", "number",
       "Leggibilita del testo (Gulpease per l'italiano, Flesch per l'inglese)", stages=("home", "plp", "pdp")),
    _k("CCL.HEADINGS", "CCL", "bool", "checks", "page", "all", "bool", "Un solo h1 e almeno un h2"),
    _k("CCL.FORM_LABELS", "CCL", "%", "checks", "page", "min", "number", "Campi con etichetta associata"),
    _k("CCL.VISUAL_COMPLEXITY", "CCL", "score", "checks", "page", "wmedian", "number",
       "Complessita visiva dallo screenshot (ancore provvisorie)"),
    _k("CCL.VALUE_PROP_CLARITY", "CCL", "label", "judgments", "site", "first", "enum",
       "Chiarezza della proposta di valore: clear | partial | unclear", judged=True),
    # ------------------------------------------------------------ MPI: persuasion supply (genuine only)
    _k("MPI.SCARCITY_SIGNALS", "MPI", "count", "checks", "site", "max", "number",
       "Segnali di scarsita genuini", credit_unless="DPR.FAKE_LOW_STOCK"),
    _k("MPI.URGENCY_SIGNALS", "MPI", "count", "checks", "site", "max", "number",
       "Segnali di urgenza con scadenza dichiarata", credit_unless="DPR.COUNTDOWN_RESET"),
    _k("MPI.RECIPROCITY", "MPI", "count", "checks", "site", "max", "number",
       "Reciprocita: spedizione/resi gratuiti, omaggi, campioni"),
    _k("MPI.AUTHORITY", "MPI", "label", "judgments", "site", "first", "enum",
       "Segnali di autorita: present | weak | absent", judged=True),
    _k("MPI.SOCIAL_PROOF_RICH", "MPI", "label", "judgments", "site", "first", "enum",
       "Social proof oltre al rating: rich | basic | absent", judged=True),
    # ------------------------------------------------------------ DPR: dark-pattern risk signals (penalty only)
    _k("DPR.COUNTDOWN_RESET", "DPR", "confidence", "deception", "site", "max", "confidence",
       "Countdown che si azzera al ricaricamento", severity=0.9),
    _k("DPR.FAKE_LOW_STOCK", "DPR", "confidence", "deception", "site", "max", "confidence",
       "Messaggio di scorte basse incoerente tra visite o prodotti", severity=0.6),
    _k("DPR.SNEAK_INTO_BASKET", "DPR", "confidence", "deception", "site", "max", "confidence",
       "Articoli aggiunti al carrello senza richiesta", severity=0.9),
    _k("DPR.PRECHECKED_PAID_ADDONS", "DPR", "confidence", "deception", "site", "max", "confidence",
       "Opzioni a pagamento pre-selezionate", severity=0.6),
    _k("DPR.CONSENT_ASYMMETRY", "DPR", "confidence", "deception", "site", "max", "confidence",
       "Banner cookie: rifiutare e piu difficile che accettare", severity=0.3),
    _k("DPR.NAGGING_OVERLAYS", "DPR", "confidence", "deception", "site", "max", "confidence",
       "Overlay ripetuti o insistenti", severity=0.3),
    _k("DPR.CONFIRMSHAMING", "DPR", "confidence", "judgments", "site", "max", "confidence",
       "Etichette di rifiuto colpevolizzanti", judged=True, severity=0.6),
    _k("DPR.TRICK_QUESTIONS", "DPR", "confidence", "judgments", "site", "max", "confidence",
       "Domande/checkbox formulate in modo ingannevole", judged=True, severity=0.6),
    _k("DPR.HIDDEN_SUBSCRIPTION", "DPR", "confidence", "judgments", "site", "max", "confidence",
       "Abbonamento ricorrente non evidente", judged=True, severity=0.9),
)

KPIS: dict[str, Kpi] = {k.id: k for k in KPI_LIST}

if len(KPIS) != len(KPI_LIST):  # pragma: no cover - import-time guard
    raise RuntimeError("Duplicate KPI id")


def by_owner(owner: str) -> list[Kpi]:
    return [k for k in KPI_LIST if k.owner == owner]


def by_producer(producer: str) -> list[Kpi]:
    return [k for k in KPI_LIST if k.producer == producer]
