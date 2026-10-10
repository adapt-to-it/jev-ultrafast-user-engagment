"""Multilingual label and URL lexicon: generic data, never per-site values.

Every entry is a regular-expression source string that compiles unchanged in JavaScript (`new RegExp(s, "i")`, no
`u` flag) and in Python (`re.compile(s, re.I)`). Portability rules: no `\\b` next to a non-ASCII letter (JS word
boundaries are ASCII-only), no `\\p{...}`, no inline flags, no named groups, no possessive or atomic groups.

lexicon_for(locale) merges the locale's lists with English, which is always included because shops in every language
use English words ("checkout", "newsletter", "shop now"). effective_language(declared, locale, sample) is the one
resolver of a page's language (its <html lang> when the lexicon has it, unless its text reads as the run locale's
language; else the run locale's): audit.js and the classifier read a page with lexicon_for() of that language, and
CCL.READABILITY picks its formula by it. lexicon_all() joins every language: CheckoutGuard refuses a forbidden
label in any language.

Forbidden labels. `pay_now` and `place_order` are matched on every page and only on control labels: they name the
act of paying or of confirming an order ("Paga ora", "Conferma e paga", "Conferma l'ordine", "Completa l'acquisto",
"Place order", "Conferma il tuo ordine", "Buy with PayPal"), never the topic of payment, so informational text such
as "Pagamenti sicuri", "Metodi di pagamento" or an instalment widget ("Paga con Klarna in 3 rate", "Pay in 3
interest-free payments") does not match. "Acquista ora" / "Buy now" is `buy_now`, a product-page shortcut that the
crawler may need; it is not forbidden by label, because CheckoutGuard refuses every click on a checkout page except
navigation away, which also covers a final "Acquista ora" button. "Procedi al pagamento" is a cart-to-checkout link in
Italian shops (`checkout`); on the checkout page itself the same guard rule blocks it. One `place_order` label is a
way into the checkout rather than an order: "Concludi ordine" (legacy WooCommerce Italian for "Proceed to checkout")
on a same-site link, outside a checkout page, whose observed href is a checkout URL. CheckoutGuard allows that link
and audit.js reports it as the cart's `checkout` control; a button with the same label stays forbidden.

URL keys (`category_url`, `product_url`, `cart_url`, `cart_action_url`, `checkout_url`, `info_url`) are matched
against the path plus the query string ("/index.php?route=checkout/checkout" is a checkout URL). `info_url` marks
content pages ("/pages/payment.html", "/content/pagamento.html") whose names would otherwise read as checkout steps.
`cart_action_url` marks a link whose GET changes the cart ("?add-to-cart=7", "/cart/change?line=1&quantity=0").
"""

import re
from functools import lru_cache

# Italian street words and articles are shared by several keys.
_L = "['’]\\s?"  # elided article: l'ordine, all'acquisto, dell'ordine
# Query keys and path segments that make a GET change the cart (WooCommerce ?add-to-cart=7 and ?remove_item=...,
# PrestaShop ?add=1, Shopify /cart/add and /cart/change), in every language: no URL carrying one is loaded as a page
# (oracles.clean_cart_url, the crawler) or reported as a product or cart link (audit.js, `cart_action_url`).
CART_ACTION_KEYS = (r"add|add[-_]to[-_]cart|add[-_]item|remove|remove[-_]item|removed[-_]item|undo[-_]item|delete"
                    r"|update|update[-_]cart|empty[-_]cart|clear[-_]cart|action|op|quantity|qty|_?wpnonce"
                    r"|aggiungi|rimuovi|elimina|svuota|aggiorna|quantit[aà]|qt[aà]|azione")
CART_ACTION_SEGMENTS = ("add", "change", "update", "clear", "remove", "delete", "empty",
                        "aggiungi", "rimuovi", "elimina", "svuota", "aggiorna", "modifica")

LEXICON: dict[str, dict[str, list[str]]] = {
    "it": {
        "add_to_cart": [
            r"aggiungi\s+(al|nel)\s+carrello", r"aggiungi\s+alla\s+(borsa|shopping\s*bag|cesta)",
            r"metti\s+nel\s+carrello", r"^\s*aggiungi\s*$", r"^\s*al\s+carrello\s*$",
            r"^\s*aggiungi\s.{1,80}\s(al|nel)\s+carrello\s*$",  # aria-label "Aggiungi <prodotto> al carrello"
        ],
        "buy_now": [
            r"(acquista|compra|ordina)\s+(ora|adesso|subito)", r"acquisto\s+(rapido|immediato)",
            r"acquista\s+con\s+1\s*-?\s*click",
        ],
        "checkout": [
            r"procedi\s+(al|all" + _L + r"|con\s+(il|l" + _L + r"))\s*(checkout|acquisto|pagamento|ordine|cassa)",
            r"vai\s+(al|alla)\s+(checkout|cassa|pagamento)", r"^\s*cassa\s*$", r"completa\s+il\s+checkout",
        ],
        "cart": [r"carrello", r"^\s*borsa\s*$", r"shopping\s*bag"],
        "search": [r"cerca", r"ricerca"],
        "filters": [r"filtr[aio]", r"affina"],
        "sort": [
            r"ordina\s+per", r"ordinamento", r"prezzo\s+(crescente|decrescente|(più|piu)\s+(basso|alto))",
            r"(più|piu)\s+(vendut[ie]|recenti|popolari)", r"novità", r"rilevanza", r"valutazion[ei]",
        ],
        "result_count": [
            r"\d[\d.]*\s+(prodott[io]|articol[io]|risultat[io]|referenze)",
            r"(mostrat[io]|visualizzat[io])\s+\d+", r"\d+\s*[-–]\s*\d+\s+di\s+\d+",
        ],
        # a search that found nothing (often above a "Ti potrebbero interessare" grid); never a cart counter
        "no_results": [
            r"nessun\s+risultat[oi]", r"\b0\s+risultat[io]\b",
            r"nessun[oa]?\s+(prodott[oi]|articol[oi]|corrispondenz[ae])\s+(trovat|corrispond|per\b)",
            r"ricerca\s+non\s+ha\s+(prodotto|restituito|dato|trovato)",
            r"non\s+(abbiamo|è\s+stato\s+possibile|siamo\s+riusciti\s+a)\s+trova",
        ],
        "load_more": [
            r"(carica|mostra|visualizza|vedi)\s+altr[io]", r"mostra\s+di\s+(più|piu)", r"carica\s+di\s+(più|piu)",
        ],
        "pagination": [
            r"pagina\s+(successiva|precedente)", r"^\s*(successiva|precedente|avanti)\s*$", r"paginazione",
        ],
        "shipping": [
            r"spedizion[ei]", r"spese\s+di\s+(spedizione|consegna)", r"costi?\s+di\s+(spedizione|consegna)",
            # a cost row labelled by its delivery ("Consegna 4,90 €", "Costo consegna"), not a delivery-time sentence
            r"^\s*(costo\s+(della\s+)?)?consegna(\s+(standard|express|espressa|rapida|a\s+domicilio))?\s*:?\s*$",
        ],
        # the cost is left to a later step: no shipping cost is shown ("Spese di spedizione calcolate al momento del
        # pagamento", Shopify's "Spedizione calcolata al checkout")
        "shipping_deferred": [
            r"(spedizion[ei]|consegna)[^.!?]{0,40}calcolat[eoia]\s+(al|alla|nel|nella|durante\s+il|in\s+fase\s+di"
            r"|al\s+momento\s+del(l" + _L + r")?)\s*(checkout|cassa|carrello|pagamento|ordine|acquisto)",
        ],
        "free_shipping": [
            r"spedizion[ei]\s*:?\s*(sempre\s+)?(gratuit[ae]|gratis|in\s+omaggio)",
            r"(gratuit[ae]|gratis)\s+(la\s+)?spedizion",
            r"consegna\s*:?\s*(sempre\s+)?(gratuita|gratis)",
        ],
        "returns": [
            r"\bres[oi]\b", r"diritto\s+di\s+recesso", r"\brecesso\b", r"rimbors[oi]", r"restituzion[ei]",
            r"cambi\s+e\s+res[oi]\b",
        ],
        "delivery": [  # a stated time or date ("Consegna in tutta Italia" and "Consegna da 4,90 €" are not)
            r"consegn[ae]\s+(prevista|stimata|garantita|entro|tra)\b", r"consegn[ae]\s+in\s+(\d|giornata)",
            r"(consegn[ae]|ricevil[oaie]|arriva)\s+(il|da|dal)\s+(\d{1,2}\s*[/.-]\s*\d{1,2}"
            r"|\d{1,2}\s+(gen|feb|mar|apr|mag|giu|lug|ago|set|ott|nov|dic)|luned|marted|mercoled|gioved|venerd|sabato"
            r"|domenica|domani)",
            r"ricevil[oaie]\s+(entro|tra|domani|oggi)\b", r"ricevil[oaie]\s+in\s+(\d|giornata)",
            r"arriva\s+(entro|tra|domani)\b", r"arriva\s+in\s+(\d|giornata)",
            r"spedit[oaie]\s+(entro|oggi|domani)\b", r"spedit[oaie]\s+in\s+(\d|giornata)",
            r"\d+\s*[-–]\s*\d+\s+giorni\s+(lavorativi|feriali)", r"tempi\s+di\s+consegna\s*:?\s*(\d|entro|in\s+\d)",
            r"spedizion[ei]\s+(in|entro)\s+\d+", r"\b\d{1,2}\s*/\s*\d{1,2}\s*(h|ore)\b",
        ],
        "vat": [
            r"\biva\s+(inclusa|compresa|esclusa|incl)", r"(incl|compres[ao]|inclus[ao])\.?\s+(l" + _L + r")?iva\b",
            r"\+\s*iva\b", r"\bprezz[io]\s+ivat[io]", r"tasse\s+incluse", r"\biva\s+\d+\s*%",
        ],
        "lowest_price_30d": [
            r"(più|piu)\s+basso\s+(degli|negli|nei|praticato\s+negli)\s+ultimi\s+30",
            r"prezzo\s+minimo\s+(degli|negli|nei)\s+ultimi\s+30", r"ultimi\s+30\s+giorni",
        ],
        "scarcity": [
            r"(solo|soltanto|ancora)\s+\d+\s+(pezz[io]|disponibil[ie]|rimast[oaie]|articol[io]|in\s+magazzino|unità)",
            r"(ultim[ie]|ultimo)\s+\d*\s*(pezz[io]|disponibil[ie]|taglie|articol[io])",
            # a number of items, not of days ("Rimasti solo 3 giorni di saldi!" is a sale's deadline)
            r"rimast[oaie]\s+(solo|soltanto)\s+\d+(?![\d.,]|\s*(giorn|or[ae]\b|minut|second|settiman|mes[ei]\b|ann[oi]\b"
            r"|gg\b|h\b))",
            r"disponibilità\s+limitata", r"scorte\s+limitate",
            r"quasi\s+esaurit[oa]", r"edizione\s+limitata", r"pezzi\s+limitati", r"in\s+esaurimento",
        ],
        "urgency": [
            r"(termina|scade|finisce|scadono|terminano)\s+(tra|fra|oggi|domani|stasera|il|a\s+mezzanotte)\b",
            r"(offerta|promozione|promo|sconto|saldi)\s+(valid[ao]|disponibile|attiv[ao])\s+(fino|solo|entro)",
            r"solo\s+(per\s+)?oggi", r"ultim[ei]\s+(ore|giorni|minuti)\b", r"entro\s+mezzanotte",
            r"(offerta|vendita)\s+lampo", r"affrettati", r"tempo\s+limitato", r"ordina\s+entro",
        ],
        "reciprocity": [
            r"omaggio", r"in\s+regalo", r"\bgratis\b", r"campion[ei]\s+(gratuit|omaggio|in\s+omaggio)",
            r"res[oi]\s+(sempre\s+)?(gratuit|gratis)", r"un\s+regalo",
            r"\d+\s*%\s+di\s+sconto\s+(sul|sulla|al|all" + _L + r")\s*(tu[oa]\s+)?(prim|iscri)",
            r"sconto\s+(del\s+)?\d+\s*%\s+(sul|sulla|sui|per)\s+(il\s+|la\s+|i\s+)?(tu[oa]\s+)?(prim|iscritt|nuov)",
        ],
        "authority": [
            r"certificat[oaie]\b", r"certificazion[ei]", r"\bpremi(o|ato|ati|ata)\b", r"riconosciment[oi]",
            r"(approvat|raccomandat|consigliat)[oaie]\s+da", r"(clinicamente|dermatologicamente)\s+testat",
            r"testat[oaie]\s+(da|clinicamente|dermatologicamente)", r"come\s+vist[oi]\s+su",
            r"(partner|fornitore)\s+ufficiale", r"\biso\s*\d{3,5}", r"\bb\s?corp\b",
        ],
        "contact": [
            r"contatt[io]", r"contattaci", r"assistenza(\s+clienti)?", r"servizio\s+clienti", r"scrivici",
        ],
        "phone": [r"\btel(efono)?\b", r"\bcell(ulare)?\b", r"chiamaci", r"numero\s+verde", r"whatsapp"],
        "address": [
            r"\b(via|viale|v\.le|piazza|p\.zza|p\.za|corso|c\.so|largo|strada|vicolo|loc\.)"
            r"\s+[a-zà-ÿ'’. ]{2,40},?\s*\d{1,4}",
            r"\b\d{5}\s+[a-zà-ÿ'’ ]{2,30}\s*\([a-z]{2}\)",
        ],
        "legal_id": [
            r"(p\.?\s?iva|partita\s+iva|p\.\s?i\.)\s*(n\.?|nr\.?|num\.?|numero)?\s*[:.°]?\s*(it)?\s?\d{11}",
            r"(c\.?\s?f\.?|codice\s+fiscale)\s*(e|/|-)\s*(p\.?\s?iva|partita\s+iva)\s*[:.°]?\s*(it)?\s?\d{11}",
            r"\b(rea|r\.e\.a\.)\s*[:.n°]*\s*[a-z]{2}\s*-?\s*\d{4,7}",
        ],
        "payment_brands": [
            r"postepay", r"satispay", r"scalapay", r"pago\s?bancomat|\bbancomat\b", r"contrassegno",
            r"bonifico(\s+bancario)?", r"\bnexi\b",
        ],
        "pay_now": [
            r"^\s*paga\s*$",
            r"\bpaga\s+(?!.*\b(rate|rata|interessi)\b)(ora|adesso|subito|in\s+sicurezza|l" + _L + r"ordine|con\b|\d|€)",
            r"\bpagare\s+ora", r"conferma\s+e\s+paga", r"conferma\s+(il\s+)?pagamento",
            r"(effettua|autorizza|completa|invia|esegui)\s+(il\s+)?pagamento", r"procedi\s+e\s+paga",
            r"\bpaga\s+e\s+(conferma|ordina)",
            r"(acquista|compra|ordina)\s+con\s+(?!.*\b(rate|rata|interessi)\b)(pay\s?pal|apple\s?pay|google\s?pay"
            r"|g\s?pay|amazon\s?pay|shop\s?pay|satispay|klarna|scalapay)",
        ],
        "place_order": [
            r"(conferma|confermo|invia|completa|concludi|effettua|inoltra|finalizza|termina)\s+(e\s+invia\s+)?(l" + _L
            + r"|il\s+|la\s+)?((tuo|vostro|mio|nostro)\s+)?(ordine|acquisto)",
            r"ordine\s+con\s+obbligo\s+di\s+pagamento", r"ordina\s+con\s+obbligo", r"acquista\s+e\s+paga",
            r"ordina\s+e\s+paga", r"conferma\s+e\s+(acquista|ordina|compra)",
        ],
        "guest": [
            r"ospite", r"senza\s+(registrar|registrazione|account|iscrizione)",
            r"non\s+(ti\s+)?serve\s+(un\s+)?account",
            r"continua\s+senza\s+(account|registr|accedere)", r"non\s+creare\s+(un\s+)?account",
            r"senza\s+(creare|aprire)\s+(un\s+)?account",
        ],
        # guest wording that its own sentence denies ("non è possibile acquistare senza creare un account"): no exit
        "guest_negated": [
            r"(non\s+(è|e'|e)\s+(più\s+)?(possibile|consentito|permesso)|non\s+(puoi|si\s+può|si\s+puo)|impossibile)"
            r"[^.;:!?,]{0,60}(senza\s+(registra|un\s+account|account|creare|aprire|iscri|accedere)|ospite)",
            r"(senza\s+(registra|un\s+account|account|creare|aprire|iscri|accedere)|ospite)[^.;:!?,]{0,40}"
            r"(non\s+(è|e'|e)\s+(più\s+)?(possibile|consentit[oa]|permess[oa]|disponibile|attiv[oa]|abilitat[oa])"
            r"|non\s+(puoi|si\s+può|si\s+puo))",
        ],
        "login": [
            r"^\s*accedi\s*$", r"accedi\s+(al|con|a)\b", r"\baccesso\b", r"il\s+mio\s+account", r"^\s*login\s*$",
            r"^\s*entra\s*$",
            r"hai\s+già\s+un\s+account", r"entra\s+nel\s+tuo\s+account",
            # a login-or-register box ("Accedi o registrati") is a login box: its password is a current password
            r"accedi\s*(o|oppure|/|\|)\s*(registrati|iscriviti|crea)",
            r"(registrati|iscriviti)\s*(o|oppure|/|\|)\s*accedi",
        ],
        "register": [  # "Utenti registrati" / "Clienti registrati" head a login box (a participle, not the imperative)
            r"(?<!utenti\s)(?<!clienti\s)(?<!già\s)registrati", r"registrazione",
            r"crea\s+(un\s+|il\s+tuo\s+|il\s+)?(account|profilo)", r"nuovo\s+cliente",
        ],
        # a password being created (account registration), not one being entered to log in ("Inserisci la password")
        "password_new": [
            r"(crea|scegli|imposta)\s+(una\s+|la\s+|la\s+tua\s+)?(nuova\s+)?password",
            r"inserisci\s+(una\s+(nuova\s+)?|la\s+nuova\s+)password", r"conferma\s+(la\s+)?password",
            r"nuova\s+password", r"ripeti\s+(la\s+)?password",
        ],
        # address fields of a delivery form (labels, names, ids): a form that holds one is a way to enter one's details
        "address_field": [
            r"^(?!.*(e-?mail|posta)).*indirizzo", r"\bcap\b", r"codice\s+postale", r"citt[àa]", r"provincia",
            r"\bcivico\b", r"localit[àa]", r"\bcomune\b", r"^\s*via\b",
        ],
        "login_gate": [
            r"(accedi|registrati)\s+per\s+(continuare|procedere|completare|acquistare|ordinare)",
            r"è\s+necessario\s+(accedere|registrarsi|creare\s+un\s+account)",
            r"devi\s+(accedere|registrarti|creare\s+un\s+account)", r"registrazione\s+obbligatoria",
            r"crea\s+(il\s+tuo\s+|un\s+)?account\s+per\s+(completare|continuare|procedere)",
        ],
        "login_optional": [  # an invitation to log in, not a gate
            r"(più|piu)\s+(velocemente|veloce|rapidamente|rapido|in\s+fretta)", r"hai\s+già\s+un\s+account",
            r"sei\s+già\s+(registrat|client)", r"(acquisto|checkout|ordine)\s+(veloce|rapido|express)",
        ],
        "optional": [r"facoltativ[oa]", r"opzional[ei]", r"non\s+obbligatori[oa]"],
        "consent_banner": [r"cookie", r"consenso", r"tracciament[oi]", r"profilazione", r"\bgdpr\b"],
        "consent_accept": [  # "Accetta solo i necessari" rejects the optional cookies (consent_reject)
            r"^\s*accett[ao](?!.*(solo|necessari|tecnici|essenziali|strettamente))",
            r"accett[ao]\s+(tutt[io]|e\s+continua|e\s+chiudi|i\s+cookie)(?!.*(solo|necessari|tecnici|essenziali))",
            r"consenti\s+(tutt[io]|i\s+cookie)", r"^\s*(s[iì],?\s+)?acconsento", r"^\s*s[iì],?\s+accett[oa]",
            r"^\s*va\s+bene\s*$", r"^\s*(ok,?\s+)?ho\s+capito",
        ],
        "consent_reject": [
            r"^\s*rifiut[ao]", r"rifiut[ao]\s+(tutt[io]|i\s+cookie)", r"nega\s+(il\s+)?consenso", r"non\s+accett[oa]",
            r"(solo|usa\s+solo)\s+(i\s+)?(cookie\s+)?(necessari|tecnici|essenziali)",
            r"^\s*(accett[ao]|consenti)\s+(solo\s+)?(i\s+)?(cookie\s+)?(strettamente\s+)?(necessari|tecnici|essenziali)",
            r"(continua|prosegui|procedi)\s+senza\s+accettare", r"^\s*nega\b", r"non\s+acconsent",
        ],
        "consent_manage": [
            r"personalizza", r"^\s*gestisci", r"gestisci\s+(le\s+)?(preferenze|cookie|opzioni|consenso)",
            r"impostazioni(\s+(dei\s+)?cookie)?", r"preferenze",
        ],
        "newsletter": [
            r"newsletter", r"iscriviti", r"\biscrizione\b", r"ricevi\s+(offerte|sconti|aggiornamenti|il\s+\d+)",
        ],
        "subscription": [
            r"abbonament[oi]", r"\babbonati\b", r"rinnovo\s+automatico", r"si\s+rinnova",
            r"rinnovat[oa]\s+automaticamente",
            r"addebito\s+(mensile|ricorrente|automatico|annuale)", r"(al|ogni|/)\s*mese\b",
            r"(all" + _L + r"|ogni|/)\s*anno\b",
            r"mensilmente", r"prova\s+gratuita", r"periodo\s+di\s+prova", r"disdici\s+(quando|in\s+qualsiasi)",
        ],
        "generic_link": [
            r"^\s*(clicca\s+qui|qui|scopri\s+di\s+(più|piu)|scopri|leggi\s+(di\s+(più|piu)|tutto|altro)|per\s+saperne\s+di"
            r"\s+(più|piu)|maggiori\s+informazioni|(più|piu)\s+informazioni|info|dettagli|vai|vedi|vedi\s+tutto|continua"
            r"|link)\s*[.!»›>→]*\s*$",
        ],
        "decline": [
            r"no,?\s+grazie", r"non\s+ora\b", r"non\s+mi\s+interessa", r"preferisco\s+(non|pagare|perdere|rinunciare)",
            r"\brinuncio\b", r"(più|piu)\s+tardi", r"^\s*salta\s*$", r"continua\s+senza", r"^\s*chiudi\s*$",
        ],
        "close": [r"^\s*chiudi", r"^\s*[×✕✖x]\s*$"],
        "variant": [
            r"\btagli[ae]\b", r"\bmisur[ae]\b", r"\bcolor[ei]\b", r"\bformato\b", r"\bvariant[ei]\b", r"capacità",
        ],
        "stock": [
            r"disponibil[ei]", r"disponibilità", r"in\s+magazzino", r"pronta\s+consegna", r"esaurit[oa]",
            r"spedizione\s+immediata",
        ],
        "out_of_stock": [r"esaurit[oa]", r"non\s+disponibile", r"\bterminat[oa]\b"],
        # the placeholder option of a variant select ("Scegli un'opzione...", "-- Seleziona --"), not a choice
        "choose_option": [r"^\W*(scegli|seleziona|selezionare|scegliere)\b", r"^\W*$"],
        "quantity": [r"quantit[àa]", r"^\s*q\.?t[àa]", r"\bqta\b"],
        "coupon": [
            r"codice\s+(sconto|promozionale|promo|coupon|regalo)", r"\bcoupon\b", r"\bbuono\b", r"carta\s+regalo",
            r"\bvoucher\b",
        ],
        "remove": [r"rimuovi", r"\belimina", r"\bcancella\b", r"\btogli\b"],
        # a control that empties the whole cart ("Svuota carrello", a cart's bare "Svuota"): the crawler's Jev offers
        # leave it out with "remove"; audit.js keeps it out of the cart link and reads no such control as a statement
        # that the cart is empty (FAI.CART_EDITABLE still counts per-line controls only)
        "clear_cart": [r"\bsvuot(a|are|alo|arlo)\b", r"\bazzera\s+(il\s+)?(carrello|borsa|cestino)"],
        "subtotal": [r"subtotale", r"(totale|somma)\s+parziale", r"totale\s+(prodotti|articoli|merce)"],
        "total": [r"^\s*totale", r"totale\s+(ordine|complessivo|da\s+pagare)", r"importo\s+totale", r"da\s+pagare"],
        # a tax line of a cart summary ("IVA 22%", "Imposte"): never an unexplained fee (checks.py)
        "tax_line": [r"\biva\b", r"\bimpost[ae]\b", r"\btasse\b"],
        "fee": [
            r"spedizion[ei]", r"\bconsegna\b", r"commission[ei]", r"supplement[oi]", r"protezione", r"assicurazion[ei]",
            r"garanzia", r"\bservizio\b", r"imballaggio", r"confezione\s+regalo", r"contributo", r"\bdiritti\b",
            r"\btass[ae]\b", r"\bspese\b", r"\bgestione\b",
        ],
        "addon": [
            r"protezione", r"assicurazion[ei]", r"garanzia\s+(estesa|aggiuntiva|premium|extra)",
            r"estensione\s+(di|della)\s+garanzia", r"donazione", r"\bdona\s+\d", r"confezione\s+regalo",
            r"spedizione\s+(prioritaria|express|protetta|assicurata)", r"servizio\s+premium",
        ],
        "installments": [r"\bin\s+\d+\s+rate\b", r"\d+\s+rate\b", r"\ba\s+rate\b", r"\brate\s+da\b", r"rateizza"],
        "unit_price": [r"/\s*(kg|g|l|lt|ml|m|pz|pezzo|unità)\b", r"\bal\s+(kg|litro|chilo|metro|pezzo)\b"],
        "proceed": [
            r"^\s*(continua|procedi|prosegui|avanti|invia|conferma|salva|registra|crea)",
            r"continua\s+(con|al|alla|verso)", r"vai\s+al(la)?\s+(pagamento|spedizione|passaggio|step)",
        ],
        "utility": [
            r"accedi", r"registrati", r"account", r"carrello", r"lista\s+(dei\s+)?desideri", r"preferiti", r"aiuto",
            r"assistenza", r"contatt", r"negozi\b", r"trova\s+(il\s+)?negozio", r"\bcerca\b", r"\bblog\b",
            r"lavora\s+con\s+noi", r"chi\s+siamo", r"\bfaq\b", r"lingua", r"spedizion", r"\bres[oi]\b", r"privacy",
            r"cookie\s+(policy|settings)", r"(informativa|politica|gestione|preferenze)\s+(dei\s+|sui\s+)?cookie",
            r"termini", r"condizioni",
        ],
        "review": [r"recension[ei]", r"valutazion[ei]", r"\bstelle\b", r"giudizi", r"opinioni", r"su\s+5\b"],
        "testimonial_heading": [
            r"dicono\s+di\s+noi", r"testimonianz[ae]", r"recensioni(\s+dei)?\s+clienti", r"cosa\s+dicono",
            r"opinioni(\s+dei)?\s+clienti", r"le\s+vostre\s+recensioni", r"^\s*recensioni\b",
        ],
        "verified": [r"acquisto\s+verificato", r"cliente\s+verificat[oa]", r"recensione\s+verificata"],
        "zoom": [r"ingrandisci", r"\bzoom\b"],
        "challenge": [
            r"verifica\s+(di\s+)?sicurezza", r"verifica\s+che\s+(tu\s+)?(sei|sia)\s+(un\s+)?(essere\s+)?umano",
            r"non\s+sono\s+un\s+robot", r"dimostra\s+(di\s+essere|che\s+sei)\s+(un\s+)?umano", r"accesso\s+negato",
            r"(attività|traffico)\s+insolit[oa]", r"controllo\s+del\s+browser", r"tieni\s+premuto",
        ],
        "empty_cart": [
            r"carrello\s+(è\s+)?vuoto", r"nessun\s+(prodotto|articolo)\s+nel\s+carrello",
            r"non\s+(hai|ci\s+sono)\s+(ancora\s+)?(prodotti|articoli)\s+nel",
        ],
        "age_gate": [r"maggiorenne", r"maggiore\s+età", r"hai\s+(almeno\s+)?18\s+anni"],
        "chat": [r"\bchat\b", r"assistente\s+virtuale", r"come\s+possiamo\s+aiutarti"],
        "privacy": [r"privacy", r"riservatezza", r"protezione\s+dei\s+dati", r"trattamento\s+dei\s+dati"],
        "terms": [r"termini", r"condizioni\s+(generali|di\s+vendita|d" + _L + r"uso|di\s+utilizzo)", r"note\s+legali"],
        "personal_field": [
            r"\bnome\b", r"cognome", r"indirizzo", r"\bcap\b", r"città", r"provincia", r"telefono", r"cellulare",
            r"e-?mail", r"codice\s+fiscale", r"\bpresso\b", r"\bcivico\b", r"nazione", r"\bpaese\b",
            r"data\s+di\s+nascita",
            r"ragione\s+sociale", r"partita\s+iva", r"utente", r"posta\s+elettronica", r"localit[àa]", r"\bcomune\b",
            r"recapito", r"^\s*via\b",
        ],
        "payment_field": [
            r"numero\s+(della\s+)?carta", r"titolare\s+(della\s+)?carta", r"scadenza", r"\bcvv\b", r"\bcvc\b",
            r"\biban\b",
            r"codice\s+di\s+sicurezza",
        ],
        "checkout_title": [
            r"^\s*checkout\b", r"^\s*cassa\b", r"dati\s+(di\s+|per\s+la\s+)?(spedizione|fatturazione|consegna)",
            r"indirizzo\s+di\s+(spedizione|consegna|fatturazione)", r"spedizione\s+e\s+pagamento",
            r"metodo\s+di\s+pagamento",
            r"completa\s+(il\s+tuo\s+)?ordine",
        ],
        "breadcrumb": [r"briciol[ae]", r"percorso", r"sei\s+qui"],
        "day_unit": [r"g", r"gg", r"giorn[oi]"],
        "rating_scale": [r"\b(su|di)\s+5\b"],
        "trust_badge": [
            r"pagament[io]\s+sicur[io]", r"sicurezza\s+garantita", r"recensioni\s+verificate", r"\bfeedaty\b",
            r"soddisfatt[io]\s+o\s+rimborsat[io]", r"garanzia\s+(di\s+)?soddisfazione", r"sigillo",
        ],
        "category_url": [
            r"/(categori[ae]|collezion[ei]|negozio|reparto|catalogo|uomo|donna|bambin[io]|saldi|offerte|outlet)"
            r"(/|$|\?|-|_|\.)",
        ],
        "product_url": [r"/(prodott[oi]|articolo|scheda)/", r"prodotto(-[a-z]+)?\."],
        "cart_url": [r"/(carrello|borsa)(/|$|\?|\.|#)", r"carrello\."],
        "checkout_url": [r"/(cassa|pagamento|ordine|acquisto)(/|$|\?|\.|#)"],
        "info_url": [
            r"/(content|contenuti|pagine|informazioni|info|aiuto|assistenza|guida|guide|faq|cms|blog|note-legali)/",
            r"/(ordine|ordini)/(stato|traccia|tracciamento|storico)", r"/(traccia|tracciamento|stato)-?ordin[ei]",
            r"/(mio-?account|il-mio-account|area-(riservata|clienti))/ordin[ei]",
        ],
    },
    "en": {
        "add_to_cart": [
            r"add\s+to\s+(cart|bag|basket|trolley)", r"^\s*add\s*$", r"^\s*add\s.{1,80}\sto\s+(cart|bag|basket)\s*$",
        ],
        "buy_now": [r"buy\s+(it\s+)?now", r"order\s+now", r"buy\s+with\s+1-?\s*click", r"^\s*shop\s+now\s*$"],
        "checkout": [
            r"(proceed|continue|go)\s+to\s+(checkout|payment)", r"secure\s+checkout",
            r"^\s*check\s*out(\s+(now|securely|here|sicuro))?\s*[→›»>(\d)]*\s*$",  # "Checkout →", "Checkout (2)"
        ],
        "cart": [r"\bcart\b", r"\bbasket\b", r"^\s*(my\s+|shopping\s+)?bag\s*$"],
        "search": [r"search", r"find\s+products"],
        "filters": [r"\bfilter", r"\brefine\b"],
        "sort": [
            r"sort\s+by", r"\bsort\b", r"price\s*:?\s*(low|high)", r"(lowest|highest)\s+price", r"\bnewest\b",
            r"best\s*sell", r"most\s+popular", r"\brelevance\b", r"top\s+rated", r"customer\s+rating",
        ],
        "result_count": [
            r"\d[\d,]*\s+(products?|items?|results?)\b", r"showing\s+\d+", r"\d+\s*[-–]\s*\d+\s+of\s+\d+",
        ],
        "no_results": [
            r"\bno\s+results?\b", r"\b0\s+results?\b", r"\bno\s+(products?|items?|matches)\s+(were\s+)?(found|match)",
            r"did\s+not\s+match\s+any", r"(couldn['’]t|could\s+not|didn['’]t|did\s+not)\s+find\s+any",
            r"\bnothing\s+(was\s+)?found\b",
        ],
        "load_more": [r"load\s+more", r"show\s+more", r"view\s+more", r"see\s+more\s+products"],
        "pagination": [r"(next|previous)\s+page", r"^\s*(next|previous|prev)\s*[»›>]*\s*$", r"pagination"],
        "shipping": [
            r"shipping", r"delivery\s+(cost|fee|charge|price)", r"postage",
            r"^\s*((standard|express|next[-\s]day|tracked|home|premium)\s+)?delivery\s*:?\s*$",  # a cost row label
        ],
        "shipping_deferred": [  # "Shipping calculated at checkout", "Taxes and shipping calculated at checkout"
            r"(shipping|delivery|postage)[^.!?]{0,40}(calculated|determined|confirmed|added)\s+(at|during|in|on)\s+"
            r"(the\s+)?(checkout|cart|basket|payment|next\s+step)",
        ],
        "free_shipping": [r"free\s+(standard\s+)?(shipping|delivery|postage)", r"(shipping|delivery)\s*:?\s*free\b"],
        "returns": [
            r"\breturns\b", r"\breturn\s+(policy|window)", r"free\s+returns?", r"\d+[-\s]day\s+returns?",
            r"\brefunds?\b", r"money[-\s]back", r"right\s+of\s+withdrawal",
        ],
        "delivery": [  # a stated time or date ("delivered in a gift box" is not)
            r"deliver(y|ed)\s+(by|within|on)\b", r"deliver(y|ed)\s+in\s+\d", r"delivery\s+(estimate|time)\b",
            r"arrives?\s+(by|on|within)\b", r"arrives?\s+in\s+\d", r"get\s+it\s+(by|on|tomorrow)\b",
            r"ships?\s+(within|today|tomorrow|by)\b", r"ships?\s+in\s+\d",
            r"\d+\s*[-–]\s*\d+\s+(business|working)\s+days", r"estimated\s+delivery",
        ],
        "vat": [
            r"(incl|including|inc)\.?\s+vat\b", r"\bvat\s+(included|incl|excluded)", r"(excl|excluding|ex)\.?\s+vat\b",
            r"tax(es)?\s+included", r"plus\s+vat\b",
        ],
        "lowest_price_30d": [
            r"lowest\s+price\s+(in|of|over|during)\s+the\s+(last|past|previous)\s+30\s+days", r"30[-\s]day\s+low",
            r"(last|past|previous)\s+30\s+days",
        ],
        "scarcity": [
            r"only\s+\d+\s+(left|remaining|in\s+stock)", r"\d+\s+left\s+in\s+stock", r"low\s+stock",
            r"limited\s+(stock|edition|availability)", r"almost\s+(gone|sold\s+out)", r"selling\s+fast",
            r"last\s+\d+\s+(items|pieces)",
        ],
        "urgency": [
            r"ends\s+(in|soon|today|tonight|at|on)\b", r"(offer|sale|deal)\s+(ends|expires|valid\s+until)",
            r"today\s+only", r"last\s+chance", r"limited[-\s]time", r"\bhurry\b", r"order\s+within",
            r"expires?\s+in\b",
        ],
        "reciprocity": [
            r"free\s+(gift|samples?|returns?)", r"free\s+\d+[-\s]day\s+returns?", r"\bcomplimentary\b",
            r"bonus\s+gift", r"\d+\s*%\s+off\s+your\s+first",
        ],
        "authority": [
            r"\bcertified\b", r"award[-\s]winning", r"\bawards?\b", r"as\s+seen\s+(on|in)\b", r"featured\s+in\b",
            r"(recommended|approved|endorsed)\s+by\b", r"clinically\s+(tested|proven)", r"dermatologist[-\s]tested",
            r"official\s+(partner|supplier)",
        ],
        "contact": [r"contact(\s+us)?", r"customer\s+(service|care|support)", r"help\s+cent(er|re)"],
        "phone": [r"\bphone\b", r"call\s+us", r"\btel\b", r"toll[-\s]free"],
        "address": [r"\b\d{1,5}\s+[a-z0-9 .'-]{2,40}\s+(street|road|avenue|boulevard|lane|drive)\b"],
        "legal_id": [
            r"\bvat\s*(no\.?|number|id|reg\.?(\s+no\.?)?)?\s*[:.]?\s*[a-z]{2}\s?[0-9a-z]{8,12}\b",
            r"company\s+(registration\s+)?(no\.?|number)\s*[:.]?\s*\d{6,10}",
        ],
        "payment_brands": [
            r"\bvisa\b", r"master\s?card", r"\bmaestro\b", r"american\s+express|\bamex\b", r"pay\s?pal", r"\bklarna\b",
            r"apple\s?pay", r"google\s?pay|\bg\s?pay\b", r"\bdiners(\s+club)?\b", r"\bjcb\b", r"union\s?pay",
            r"amazon\s?pay", r"shop\s?pay", r"bancontact", r"\bsofort\b", r"cash\s+on\s+delivery", r"bank\s+transfer",
        ],
        "pay_now": [
            r"^\s*pay\s*$", r"\bpay\s+(?!.*\b(installments?|instalments?|interest)\b)(now|securely|with|\d|€|\$|£)",
            r"(confirm|complete|submit|make|authori[sz]e)\s+"
            r"(the\s+|your\s+)?payment", r"confirm\s+(and|&)\s+pay",
            r"buy\s+with\s+(?!.*\b(installments?|instalments?|interest)\b)(apple\s?pay|google\s?pay|g\s?pay|pay\s?pal"
            r"|shop\s?pay|amazon\s?pay|klarna|venmo|link)\b",
            r"check\s*out\s+with\s+(paypal|apple\s?pay|google\s?pay|shop\s?pay|amazon\s?pay)",
        ],
        "place_order": [
            r"place\s+(my\s+|your\s+|the\s+)?order",
            r"(confirm|submit|complete)\s+(my\s+|your\s+|the\s+)?(order|purchase)",
            r"order\s+(and|&)\s+pay", r"order\s+with\s+obligation\s+to\s+pay", r"buy\s+and\s+pay",
            r"complete\s+checkout", r"(finish|finali[sz]e)\s+(my\s+|your\s+|the\s+)?(order|purchase)",
            r"confirm\s+(and|&)\s+buy",
        ],
        "guest": [
            r"\bguest\b", r"without\s+(an\s+)?account", r"no\s+account\s+(needed|required)",
            r"continue\s+without\s+(signing|registering|an\s+account)", r"without\s+creating\s+(an\s+)?account",
            r"(do\s+not|don'?t)\s+create\s+(an?\s+)?(customer\s+)?account",
        ],
        "guest_negated": [
            r"(cannot|can['’]?t|can\s+not|unable\s+to|not\s+(possible|able|allowed|permitted)\s+to)[^.;:!?,]{0,60}"
            r"(without\s+(an?\s+(customer\s+)?account|creating|registering|signing|logging)|\bguest\b)",
            r"(without\s+(an?\s+(customer\s+)?account|creating|registering|signing|logging)|\bguest\b)[^.;:!?,]{0,40}"
            r"((is|are)\s+not|isn['’]t|aren['’]t)\s+(possible|allowed|available|permitted|supported|enabled)",
            r"(without\s+(an?\s+(customer\s+)?account|creating|registering|signing|logging)|\bguest\b)[^.;:!?,]{0,40}"
            r"(is|are)\s+(unavailable|disabled)",
        ],
        "login": [r"sign\s*in", r"log\s*in", r"my\s+account", r"already\s+have\s+an\s+account"],
        "register": [r"\bregister\b", r"sign\s*up", r"create\s+(an\s+|your\s+)?account", r"new\s+customer"],
        "password_new": [
            r"(create|choose|set)\s+(a\s+|your\s+)?(new\s+)?password", r"confirm\s+(your\s+)?password",
            r"new\s+password", r"re-?\s?enter\s+(your\s+)?password", r"repeat\s+(your\s+)?password",
        ],
        "address_field": [
            r"^(?!.*e-?mail).*address", r"street", r"city", r"\btown\b", r"zip", r"post\s*code", r"postal",
            r"\bcounty\b",
        ],
        "login_gate": [
            r"(sign\s*in|log\s*in|register)\s+to\s+(continue|check\s*out|proceed|purchase)",
            r"you\s+must\s+(sign\s*in|log\s*in|register|create\s+an\s+account)", r"account\s+(is\s+)?required",
            r"create\s+an\s+account\s+to\s+(continue|complete|proceed)",
        ],
        "login_optional": [
            r"\b(faster|quicker|speed\s+up)\b", r"(already\s+)?have\s+an\s+account", r"returning\s+customer",
            r"express\s+checkout",
        ],
        "optional": [r"\boptional\b"],
        "consent_banner": [r"cookie", r"\bconsent\b", r"\btracking\b", r"\bgdpr\b"],
        "consent_accept": [  # "Accept only essential cookies" rejects the optional cookies (consent_reject)
            r"^\s*accept(?!.*\b(only|necessary|essential|required|strictly)\b)",
            r"accept\s+(all|cookies)(?!.*\b(only|necessary|essential|required|strictly)\b)", r"allow\s+(all|cookies)",
            r"^\s*(i\s+)?agree", r"^\s*got\s+it", r"^\s*ok(ay)?\b",
        ],
        "consent_reject": [
            r"^\s*reject", r"reject\s+all", r"^\s*decline", r"^\s*deny", r"^\s*refuse",
            r"(necessary|essential)\s+(cookies\s+)?only", r"only\s+(necessary|essential)",
            r"^\s*(accept|allow|use)\s+(only\s+)?(the\s+)?(strictly\s+)?(necessary|essential|required)\b",
            r"continue\s+without\s+accepting", r"do\s+not\s+accept", r"disagree", r"do\s+not\s+agree",
            r"only\s+allow\s+(necessary|essential)",
        ],
        "consent_manage": [
            r"customi[sz]e", r"^\s*manage", r"manage\s+(preferences|cookies|options|settings)", r"(cookie\s+)?settings",
            r"preferences", r"more\s+options",
        ],
        "newsletter": [r"newsletter", r"subscribe", r"sign\s+up\s+(for|to)", r"join\s+(our|the)\s+(list|club|mailing)"],
        "subscription": [
            r"subscription", r"subscribe\s+(&|and)\s+save", r"auto[-\s]?renew",
            r"renews?\s+(automatically|monthly|annually)",
            r"(per|/)\s*(month|mo|year|yr)\b", r"\bmonthly\b", r"free\s+trial", r"\bmembership\b", r"cancel\s+anytime",
            r"\brecurring\b",
        ],
        "generic_link": [
            r"^\s*(click\s+here|here|read\s+more|learn\s+more|more|more\s+info|details|see\s+more|view\s+more|"
            r"find\s+out\s+more|go|link|this\s+link)\s*[.!»›>→]*\s*$",
        ],
        "decline": [
            r"no,?\s+thanks", r"not\s+now", r"maybe\s+later", r"i['’]?d\s+rather", r"i\s+don['’]?t\s+want",
            r"no,?\s+i\s+(prefer|don)", r"^\s*skip\s*$", r"^\s*dismiss\s*$", r"^\s*close\s*$",
        ],
        "close": [r"^\s*close", r"^\s*dismiss"],
        "variant": [r"\bsize\b", r"\bcolou?r\b", r"\bvariant\b", r"\bcapacity\b"],
        "stock": [r"in\s+stock", r"out\s+of\s+stock", r"sold\s+out", r"\bavailable\b", r"\bunavailable\b"],
        "out_of_stock": [r"out\s+of\s+stock", r"sold\s+out", r"\bunavailable\b"],
        "choose_option": [r"^\W*(choose|select|pick)\b", r"^\W*$"],
        "quantity": [r"quantity", r"\bqty\b"],
        "coupon": [r"coupon", r"promo(tional)?\s+code", r"discount\s+code", r"voucher", r"gift\s+card"],
        "remove": [r"\bremove\b", r"\bdelete\b"],
        "clear_cart": [
            r"\b(empty|clear)\s+(the\s+|your\s+|my\s+)?(shopping\s+)?(cart|bag|basket|trolley)\b",
            r"^\s*(empty|clear)(\s+all)?\s*$",
        ],
        "subtotal": [r"sub[-\s]?total", r"items\s+total"],
        "total": [r"^\s*total", r"order\s+total", r"grand\s+total", r"amount\s+due", r"total\s+to\s+pay"],
        # English plus the tax names of other languages (MwSt, TVA, IVA...): every language's lexicon merges this list
        "tax_line": [
            r"\bvat\b", r"\btax(es)?\b", r"sales\s+tax", r"\b(iva|impost[ae]|tasse|mwst|ust|tva|btw|gst|hst)\b",
        ],
        "fee": [
            r"shipping", r"\bdelivery\b", r"\bfee\b", r"surcharge", r"protection", r"insurance", r"warranty",
            r"\bservice\b", r"handling", r"packaging", r"gift\s+wrap", r"\btax\b",
        ],
        "addon": [
            r"protection", r"insurance", r"extended\s+warranty", r"donation", r"\bdonate\b", r"gift\s+wrap",
            r"priority\s+(shipping|handling)", r"premium\s+service",
        ],
        "installments": [r"\d+\s+(interest[-\s]free\s+)?(payments|installments|instalments)\b", r"pay\s+in\s+\d+\b"],
        "unit_price": [r"/\s*(kg|lb|oz|g|l|ml|m|ea|unit)\b", r"\bper\s+(kg|lb|oz|litre|liter|unit)\b"],
        "proceed": [
            r"^\s*(continue|proceed|next|submit|confirm|save|register|create)", r"go\s+to\s+(payment|shipping|next)",
        ],
        "utility": [
            r"sign\s*in", r"log\s*in", r"\bregister\b", r"\baccount\b", r"\bcart\b", r"\bbasket\b",
            r"^\s*(my\s+|shopping\s+)?bag\s*(\(\d+\))?\s*$",
            r"wish\s*list", r"favou?rites", r"\bhelp\b", r"\bsupport\b", r"\bcontact\b", r"\bstores?\b",
            r"store\s+locator", r"\bsearch\b", r"\bblog\b", r"careers", r"\babout\b", r"\bfaq\b", r"language",
            r"shipping", r"\breturns\b", r"privacy", r"cookie\s+(policy|settings|preferences|notice)",
            r"(manage|about)\s+cookies", r"\bterms\b",
        ],
        "review": [r"\breviews?\b", r"\bratings?\b", r"\bstars?\b", r"out\s+of\s+5\b"],
        "testimonial_heading": [
            r"testimonials?", r"what\s+(our\s+)?customers\s+say", r"customer\s+reviews", r"^\s*reviews\b",
        ],
        "verified": [r"verified\s+(purchase|buyer|customer|review)"],
        "zoom": [r"\bzoom\b", r"\benlarge\b"],
        "challenge": [
            r"captcha", r"verify\s+(that\s+)?you\s+are\s+(a\s+)?human", r"are\s+you\s+a\s+robot",
            r"i['’]?m\s+not\s+a\s+robot", r"checking\s+(if\s+the\s+site\s+connection\s+is\s+secure|your\s+browser)",
            r"just\s+a\s+moment", r"access\s+denied", r"unusual\s+traffic", r"press\s+(&|and)\s+hold",
            r"attention\s+required", r"please\s+enable\s+(javascript|cookies)", r"bot\s+(detection|protection)",
            r"security\s+check",
        ],
        "empty_cart": [
            r"(cart|bag|basket)\s+is\s+(currently\s+)?empty", r"empty\s+(cart|bag|basket)",
            r"no\s+items\s+in\s+your\s+(cart|bag|basket)",
        ],
        "age_gate": [
            r"are\s+you\s+(over\s+)?(18|21)", r"age\s+verification", r"legal\s+(drinking\s+)?age", r"\bover\s+18\b",
        ],
        "chat": [r"\bchat\b", r"how\s+can\s+we\s+help", r"virtual\s+assistant"],
        "privacy": [r"privacy", r"data\s+protection"],
        "terms": [r"\bterms\b", r"conditions\s+of\s+(sale|use)", r"legal\s+notice"],
        "personal_field": [
            r"\bname\b", r"\baddress\b", r"\bcity\b", r"\bzip\b", r"postal", r"postcode", r"\bphone\b", r"e-?mail",
            r"\bcountry\b", r"\bstate\b", r"\bcounty\b", r"birth", r"username", r"company", r"surname", r"\btown\b",
            r"post\s*code", r"telephone", r"\bmobile\b", r"\bstreet\b",
        ],
        "payment_field": [
            r"card\s*number", r"cardholder", r"name\s+on\s+card", r"expir", r"security\s+code", r"\bcvv\b", r"\bcvc\b",
            r"\biban\b",
        ],
        "checkout_title": [
            r"^\s*(secure\s+)?checkout\b", r"shipping\s+(address|details|information)", r"billing\s+(address|details)",
            r"payment\s+(method|details|information)",
        ],
        "breadcrumb": [r"breadcrumbs?", r"you\s+are\s+here"],
        "day_unit": [r"d", r"days?"],
        "rating_scale": [r"\bout\s+of\s+5\b", r"\bof\s+5\b"],
        "trust_badge": [
            r"trustpilot", r"trusted\s*shops", r"\bnorton\b", r"\bmcafee\b", r"\bssl\b",
            r"secure\s+(payments?|checkout)",
            r"verified\s+reviews", r"money[-\s]back\s+guarantee", r"satisfaction\s+guarantee",
        ],
        "category_url": [
            r"/(c|cat|category|categories|collections?|shop|department|catalog|catalogue|listing|men|women|kids|sale)"
            r"(/|$|\?|-|_|\.)", r"category\.", r"[?&](cat|category|category_id)=",
        ],
        "product_url": [
            r"/(p|prod|product|products|item|dp)/", r"/[a-z0-9-]+-p-?\d{3,}", r"product(-[a-z]+)?\.",
            r"[?&](pid|product_id|sku)=",
        ],
        "cart_url": [r"/(cart|basket|bag|shopping-?bag)(/|$|\?|\.|#)", r"cart\."],
        # path and decoded query of a URL whose GET changes the cart (CART_ACTION_KEYS, CART_ACTION_SEGMENTS)
        "cart_action_url": [rf"[?&](?:{CART_ACTION_KEYS})(?==|&|$)",
                            rf"/(?:{'|'.join(CART_ACTION_SEGMENTS)})(?=/|\?|$)"],
        "checkout_url": [r"/(checkouts?|payment|onepage)(/|$|\?|\.|#)", r"checkout\."],
        "info_url": [
            r"/(pages?|help|support|policies|policy|legal|faq|blog|content|cms|info)/",
            r"/orders?/(status|track|tracking|history)", r"/(track|tracking)-?(my-)?orders?",
            r"/order-(status|tracking)",
            r"/(account|my-?account|customer/account)/orders?",
        ],
    },
}

# Multi-label public suffixes for registrable-domain comparison (an approximation of the Public Suffix List: the
# common two-level ccTLDs and hosting platforms where each subdomain is a different site).
PUBLIC_SUFFIXES = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "ltd.uk", "plc.uk", "net.uk", "com.au", "net.au", "org.au",
    "co.nz", "org.nz", "co.jp", "ne.jp", "or.jp", "co.kr", "com.br", "com.mx", "com.ar", "com.co", "com.tr",
    "com.cn", "com.hk", "com.sg", "com.tw", "co.in", "co.za", "co.il", "com.my", "com.ph", "com.pe", "com.ua",
    "com.pl", "com.gr", "com.cy", "com.mt", "co.at", "or.at", "gv.at", "co.hu", "com.pt", "com.es", "com.ru",
    "myshopify.com", "github.io", "netlify.app", "vercel.app", "herokuapp.com", "appspot.com", "blogspot.com",
    "pages.dev", "web.app", "firebaseapp.com", "azurewebsites.net", "cloudfront.net", "wixsite.com",
    "squarespace.com", "wordpress.com", "altervista.org", "webnode.it", "webnode.com", "webnode.page",
    "jimdosite.com", "jimdofree.com", "weebly.com", "webflow.io", "square.site", "business.site", "company.site",
    "bigcartel.com", "mystrikingly.com", "tilda.ws", "onrender.com",
})
# The second level of a two-letter ccTLD that is a public suffix almost everywhere ("com.vn", "co.id", "gob.mx",
# "org.br"): an unlisted one keeps three labels, so a whole namespace never reads as one site. audit.js siteOf()
# applies the same rule.
GENERIC_SECOND_LEVEL = frozenset({"ac", "co", "com", "edu", "gob", "go", "gov", "gv", "ltd", "me", "mil", "ne", "net",
                                  "nom", "or", "org", "plc", "sch"})


def registrable_domain(host: str | None) -> str:
    """"shop.example.co.uk" -> "example.co.uk", "shop.example.com.vn" -> "example.com.vn", "mionegozio.altervista.org"
    stays itself; IP addresses and single-label hosts are returned unchanged."""
    host = (host or "").strip().lower().rstrip(".")
    if not host or ":" in host or re.fullmatch(r"[\d.]+", host) or "." not in host:
        return host  # IPv6 / IPv4 / localhost
    labels = host.split(".")
    for size in (3, 2):
        if len(labels) > size and ".".join(labels[-size:]) in PUBLIC_SUFFIXES:
            return ".".join(labels[-size - 1:])
    if len(labels) > 2 and len(labels[-1]) == 2 and labels[-2] in GENERIC_SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


@lru_cache(maxsize=16)
def _merged(base: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    local, english = LEXICON.get(base, {}), LEXICON["en"]
    keys = list(dict.fromkeys([*local, *english]))
    return tuple((k, tuple(dict.fromkeys([*local.get(k, []), *english.get(k, [])]))) for k in keys)


def _base(language: str | None) -> str:
    return (language or "").strip().split("-")[0].split("_")[0].lower()


def lexicon_for(locale: str) -> dict[str, list[str]]:
    """Patterns of the locale's language ("it", "it-IT") merged with English; unknown languages get English."""
    base = _base(locale) or "en"
    return {key: list(patterns) for key, patterns in _merged(base)}


@lru_cache(maxsize=1)
def _all() -> tuple[tuple[str, tuple[str, ...]], ...]:
    keys = list(dict.fromkeys(k for language in LEXICON.values() for k in language))
    return tuple((k, tuple(dict.fromkeys(p for language in LEXICON.values() for p in language.get(k, []))))
                 for k in keys)


def lexicon_all() -> dict[str, list[str]]:
    """Every language's patterns per key (CheckoutGuard: a forbidden label is refused in any language)."""
    return {key: list(patterns) for key, patterns in _all()}


# Function words that tell the language of a page's text, for a declared <html lang> that may be a theme default.
FUNCTION_WORDS = {
    "it": frozenset("il lo la le gli di del dello della delle dei degli che per con non una uno sono alla alle nel "
                    "nella nelle sul sulla dal dalla anche più come questo questa ogni tutti tutte ai al da ed è"
                    .split()),
    "en": frozenset("the and of to with for is are you your our this that on at from by it be all more an or not we"
                    .split()),
}


def text_languages(sample: str | None) -> dict[str, int]:
    """How many function words of each FUNCTION_WORDS language the text holds."""
    words = re.findall(r"[^\W\d_]+", (sample or "").lower())
    return {language: sum(w in vocabulary for w in words) for language, vocabulary in FUNCTION_WORDS.items()}


def effective_language(declared: str | None, locale: str, sample: str | None = None) -> str:
    """The language a page's words are matched in and its text is read in: the primary subtag of its declared
    <html lang> when the lexicon has that language, else the run locale's ("it-IT" page -> "it"; "de" page on an
    Italian run -> "it"). A declared language other than the run's gives way to the run's when the page's text
    (sample) reads as the run's language: at least 5 of its function words and twice as many as the declared one's
    (an Italian shop whose theme declares lang="en" is read in Italian, whose lexicon includes English)."""
    page, run = _base(declared), _base(locale) or "en"
    if page not in LEXICON:
        return run
    if sample and page != run and page in FUNCTION_WORDS and run in FUNCTION_WORDS:
        counts = text_languages(sample)
        if counts[run] >= max(5, 2 * counts[page]):
            return run
    return page


def compile_lexicon(lexicon: dict[str, list[str]]) -> dict[str, re.Pattern]:
    """One case-insensitive alternation per key (Python side of the same sources audit.js compiles)."""
    return {key: re.compile("|".join(f"(?:{p})" for p in patterns), re.I) for key, patterns in lexicon.items()
            if patterns}
