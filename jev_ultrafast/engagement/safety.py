"""CheckoutGuard: the hard limits every engagement action passes, for the crawler and the journey runner alike.

- Never click a control whose label pays (lexicon `pay_now`), on any page. Never click one whose label confirms an
  order (`place_order`), on any page, with one exception, the checkout entry: a link (role link, kind click) on a
  page that is not a checkout page, whose observed href is a same-site http(s) checkout URL (not a cart URL) on
  another path. "Concludi ordine" is how legacy WooCommerce Italian says "Proceed to checkout"; a GET into the stage
  where the run stops anyway cannot place an order. Buttons, submits, "#" or script hrefs and other sites stay refused.
- A page is a checkout page when the classifier says so, when its observed fields ask for payment data (a one-page
  checkout classified as a cart: a field labelled CVV/CVC/CSC, or two payment-field words over the labels of the
  observed fill/select fields and the text of their form, one of them not a best-before date or an IBAN), or when
  its URL (path and query: OpenCart routes "index.php?route=checkout/checkout") is a checkout URL that is not also a
  cart URL (Magento "/checkout/cart/" stays a cart) nor a content page ("/pages/payment.html"). The viewport text is
  never evidence: it depends on the scroll position, and a product description or a footer may name a CVV. On a
  checkout page only scrolling, waiting and links that lead away are allowed: the
  link's observed href (snapshot.js guard data) must be an http(s) navigation to another path that is not a further
  checkout step, and its label must not name a step onward. No field, choice, button or submit.
- Typing is default-deny. Never into password, payment (cc-*, card, CVV, IBAN), personal-data (name, email, phone,
  address, fiscal code, birth date...) or newsletter fields, nor into a form that holds a password or payment field,
  nor on a checkout page, nor into a search box whose label asks for an email address or a phone number. What
  remains is allowed only for search fields, labelled quantity fields and coupon fields.
- Nothing is done on a page of another registrable domain than the start URL's.
- The run stops at the first checkout page (stop_at="checkout_entry"), or at the cart (stop_at="cart").
- The guard is language-independent: the lexicon it is given is joined with every language's patterns, so "Paga ora"
  is refused on a run in English and "Place order" on a run in Italian.

Labels and observed nodes come from snapshot.js (window.__jevFast); the guard never builds selectors.
"""

import json
import re
from urllib.parse import urljoin, urlsplit

from .lexicon import compile_lexicon, lexicon_all, registrable_domain

CONTROL_KINDS = ("scroll", "wait")
STOP_TYPES = {"checkout_entry": ("checkout",), "cart": ("cart", "checkout")}
# Autofill tokens (HTML autocomplete) that name personal or account data; "cc-*" is handled apart.
PERSONAL_TOKENS = re.compile(
    r"^(name|honorific-prefix|given-name|additional-name|family-name|honorific-suffix|nickname|username|new-password|"
    r"current-password|one-time-code|organization|organization-title|street-address|address-line[123]|"
    r"address-level[1-4]|country|country-name|postal-code|bday(-[a-z]+)?|sex|tel(-[a-z-]+)?|email|impp|photo|"
    r"transaction-[a-z]+)$")
PROTECTED_TYPES = ("password", "email", "tel", "file", "hidden", "date")
# Conventional field names and ids (programmer identifiers, not page language), matched after "_", "-", ".", "[", "]"
# become spaces: "billing_first_name" -> "billing first name", "FNAME" stays "FNAME".
PERSONAL_NAMES = re.compile(
    r"(e[- ]?mail|phone|tel(efono)?|mobile|cell|first.?name|last.?name|full.?name|sur.?name|nome|cognome|street|"
    r"addr|indirizzo|city|citta|town|zip|post.?code|postal|\bcap\b|province|provincia|country|paese|birth|dob|"
    r"nascita|fiscal|codice.?fiscale|\bcf\b|vat|p\.?\s?iva|partita.?iva|company|ragione.?sociale|user.?name|login|"
    r"^(f|l|first|last|full|given|family)?\s?name$)", re.I)
PAYMENT_NAMES = re.compile(r"(\bcc\b|cc[-_]?(num|number|exp|csc|cvc|name)|card|carta|cvv|cvc|csc|expir|scadenza|iban|"
                           r"pass(word)?|\bpwd\b|\bpin\b|otp)", re.I)
SEARCH_NAMES = re.compile(r"^(q|s|query|search|searchterm|search_query|keywords?|k|term)$", re.I)
# Contact data a search box may ask for ("Cerca il tuo ordine (email)"); a bare "telefono" may be a product category.
CONTACT_WORDS = re.compile(r"e-?mail|posta\s+elettronica|numero\s+di\s+(telefono|cellulare)|(phone|mobile)\s+number",
                           re.I)
PAYMENT_TEXT = re.compile(r"\b(cvv2?|cvc2?|csc)\b", re.I)
# Payment-field words that also live outside a payment form (a best-before date, a bank transfer IBAN in a footer):
# two of them alone do not make a checkout page.
WEAK_PAYMENT_WORDS = re.compile(r"^(scadenza|iban|expir)$", re.I)
ONWARD_KEYS = ("proceed", "checkout", "checkout_title", "register", "login", "buy_now", "add_to_cart")
GUARD_HREF = 12  # snapshot.js cache.guard(): index of the raw href attribute
GUARD_SCOPE = 13  # snapshot.js cache.guard(): innerText of the field's form (or nearest dialog, article, row, parent)
FIELD_KINDS = ("fill", "select")

FIELD_JS = """(node => {
  const e = window.__jevFast?.nodes.get(node);
  if (!e || !e.isConnected) return null;
  const form = e.form || e.closest('form'), scope = form || null;
  const fields = scope ? [...scope.querySelectorAll('input,select,textarea')] : [];
  const text = n => (n && (n.innerText || n.textContent) || '').replace(/\\s+/g, ' ').trim();
  const by = (e.getAttribute('aria-labelledby') || '').split(/\\s+/).filter(Boolean)
    .map(id => text(document.getElementById(id))).join(' ');
  return {tag: e.tagName.toLowerCase(), type: (e.getAttribute('type') || '').toLowerCase(),
    autocomplete: (e.getAttribute('autocomplete') || '').toLowerCase(), name: e.getAttribute('name') || '',
    inputmode: (e.getAttribute('inputmode') || '').toLowerCase(),
    id: e.id || '', placeholder: e.getAttribute('placeholder') || '', aria: e.getAttribute('aria-label') || '',
    label: ([...(e.labels || [])].map(text).join(' ') + ' ' + by).trim().slice(0, 200),
    role: e.getAttribute('role') || '', in_search: !!e.closest('[role="search"],search'),
    form_action: form ? (form.getAttribute('action') || '') : '',
    form_text: form ? [form.getAttribute('aria-label'), form.getAttribute('name'), form.id,
      typeof form.className === 'string' ? form.className : ''].filter(Boolean).join(' ').slice(0, 200) : '',
    form_password: fields.some(f => f.type === 'password'),
    form_payment: fields.some(f => /^cc-/.test((f.getAttribute('autocomplete') || '').toLowerCase()))};
})(%s)"""


class CheckoutGuard:
    def __init__(self, start_url: str, lexicon: dict, *, stop_at: str = "checkout_entry"):
        if stop_at not in STOP_TYPES:
            raise ValueError(f"stop_at must be one of {list(STOP_TYPES)}")
        self.start_url, self.stop_at = start_url, stop_at
        self.site = self._site(start_url)
        every = lexicon_all()
        self.lx = compile_lexicon({key: list(dict.fromkeys([*lexicon.get(key, []), *every.get(key, [])]))
                                   for key in dict.fromkeys([*lexicon, *every])})

    # ---------------------------------------------------------------- pages
    @staticmethod
    def _site(url: str) -> str | None:
        parts = urlsplit(url or "")
        return registrable_domain(parts.hostname) if parts.scheme in ("http", "https") and parts.hostname else None

    def same_site(self, url: str) -> bool:
        """Same registrable domain as the start URL, any port. about:blank and an empty URL count as the same site."""
        if not url or url == "about:blank":
            return True
        site = self._site(url)
        return site is not None and site == self.site

    def should_stop(self, page_type: str | None, page: dict | None = None) -> bool:
        """The stop page type; with the observed page, also any page is_checkout() recognises."""
        if page_type in STOP_TYPES[self.stop_at]:
            return True
        return page is not None and self.is_checkout(page, page_type)

    def _checkout_path(self, parts) -> bool:
        """A checkout URL (path and query) that is not a cart URL nor a content page."""
        url = (parts.path or "/") + ("?" + parts.query if parts.query else "")
        return (self._hit("checkout_url", url) and not self._hit("cart_url", url)
                and not self._hit("info_url", url))

    def is_checkout(self, page: dict, page_type: str | None) -> bool:
        """The classifier's verdict; observed payment fields (_payment_fields); a checkout URL that is not also a cart
        URL, whatever the classifier says (a one-page checkout whose cart table makes it look like a cart). Never the
        page's viewport text."""
        if page_type == "checkout":
            return True
        return self._payment_fields(page) or self._checkout_path(urlsplit(page.get("url") or ""))

    def _payment_fields(self, page: dict) -> bool:
        """A fill/select field labelled CVV/CVC/CSC, or two distinct payment-field words (the CVV token is one) over
        the labels of the observed fill/select fields and the text of their form, one of them not a best-before date
        or an IBAN ("Scadenza", "IBAN" also live in product forms and bank-transfer notes)."""
        labels, scopes = [], {}
        for action in page.get("actions") or []:
            if action.get("kind") not in FIELD_KINDS:
                continue
            labels.append(" ".join(str(action.get("label") or "").split()))
            guard = (page.get("guards") or {}).get(str(action.get("node")))
            if isinstance(guard, (list, tuple)) and len(guard) > GUARD_SCOPE and isinstance(guard[GUARD_SCOPE], str):
                scopes[guard[GUARD_SCOPE]] = None
        if any(PAYMENT_TEXT.search(label) for label in labels):
            return True
        text = "\n".join([*labels, *scopes])
        field_words = self.lx.get("payment_field")
        hits = {" ".join(m.group(0).lower().split()) for m in field_words.finditer(text)} if field_words else set()
        hits = {"cvv" if PAYMENT_TEXT.fullmatch(h) else h for h in hits}  # CVV, CVC and CSC are one word
        if PAYMENT_TEXT.search(text):
            hits.add("cvv")
        return len(hits) >= 2 and any(not WEAK_PAYMENT_WORDS.match(h) for h in hits)

    # ---------------------------------------------------------------- actions
    def _hit(self, key: str, text: str) -> bool:
        return bool(text and key in self.lx and self.lx[key].search(text))

    def forbidden(self, label: str) -> str | None:
        """'pay_now' or 'place_order' when the control label pays or confirms an order."""
        for key in ("pay_now", "place_order"):
            if self._hit(key, label or ""):
                return key
        return None

    def allowed_action(self, action: dict, page: dict, page_type: str | None) -> tuple[bool, str | None]:
        kind, label = action.get("kind"), " ".join(str(action.get("label") or "").split())
        if not self.same_site(page.get("url") or ""):
            return (kind in CONTROL_KINDS, None if kind in CONTROL_KINDS else "external_page")
        if kind in CONTROL_KINDS:
            return True, None
        keys = [k for k in (self.forbidden(label), self.forbidden(str(action.get("value") or ""))) if k]
        if keys and set(keys) == {"place_order"} and self._checkout_entry(action, page, page_type):
            keys = []
        if keys:
            return False, f"forbidden_label:{keys[0]}"
        if self.is_checkout(page, page_type):
            if kind == "click" and action.get("role") == "link":
                return self._leaves_checkout(action, label, page)
            return False, "checkout_form" if kind in ("fill", "select") else "checkout_submit"
        personal = self._hit("payment_field", label) or self._hit("personal_field", label)
        if kind == "fill" and personal and not self._hit("search", label):
            return False, "personal_field"
        return True, None

    @staticmethod
    def _target(action: dict, page: dict):
        """The link's observed href (snapshot.js guard tuple) resolved against the page: (here, target) split URLs,
        or None when the snapshot reported no href."""
        guard = (page.get("guards") or {}).get(str(action.get("node")))
        href = guard[GUARD_HREF] if isinstance(guard, (list, tuple)) and len(guard) > GUARD_HREF else None
        if not isinstance(href, str) or not href.strip():
            return None
        return urlsplit(page.get("url") or ""), urlsplit(urljoin(page.get("url") or "", href.strip()))

    @staticmethod
    def _navigates(here, target) -> bool:
        return target.scheme in ("http", "https") and (target.netloc, target.path or "/") != (here.netloc,
                                                                                            here.path or "/")

    def _checkout_entry(self, action: dict, page: dict, page_type: str | None) -> bool:
        """A place_order-labelled link that only leads into the checkout: see the module docstring."""
        if action.get("kind") != "click" or action.get("role") != "link" or self.is_checkout(page, page_type):
            return False
        urls = self._target(action, page)
        if urls is None or not self._navigates(*urls):
            return False
        target = urls[1]
        return self.same_site(target.geturl()) and self._checkout_path(target)

    def _leaves_checkout(self, action: dict, label: str, page: dict) -> tuple[bool, str | None]:
        """A link on a checkout page passes only when its observed href navigates away, e.g. back to the cart.
        A link-shaped submit (href "#" or "javascript:" with a script) or a link to the next step does not."""
        if any(self._hit(key, label) for key in ONWARD_KEYS):
            return False, "checkout_submit"
        urls = self._target(action, page)
        if urls is None:
            return False, "checkout_link_unverified"
        if not self._navigates(*urls):
            return False, "checkout_link_not_navigation"
        if self._checkout_path(urls[1]):
            return False, "checkout_submit"
        return True, None

    def filter_actions(self, page: dict, page_type: str | None) -> tuple[list[dict], list[str]]:
        allowed, notes = [], []
        if not self.same_site(page.get("url") or ""):
            notes.append(f"external page {urlsplit(page.get('url') or '').hostname}: only scrolling and waiting")
        for action in page.get("actions") or []:
            ok, reason = self.allowed_action(action, page, page_type)
            if ok:
                allowed.append(action)
            elif reason != "external_page":
                notes.append(f"{action.get('id', '?')} '{str(action.get('label') or '')[:40]}': {reason}")
        return allowed, notes

    def allow_text(self, browser, action: dict, page_type: str | None = None) -> tuple[bool, str | None]:
        """Inspect the observed field itself (type, autocomplete, name, id, label) before any typing. Default-deny:
        after every refusal, only search fields, quantity fields (numeric and labelled as a quantity) and coupon
        fields are allowed; anything else, free-text areas and unlabelled inputs included, is "field_not_allowed"."""
        if action.get("kind") != "fill":
            return False, "not_a_text_field"
        if page_type == "checkout":
            return False, "checkout_form"
        node = action.get("node")
        if type(node) is not int:
            return False, "invalid_node"
        try:
            info = browser.evaluate(FIELD_JS % json.dumps(node))
        except (RuntimeError, ValueError):
            info = None
        if not isinstance(info, dict):
            return False, "node_not_found"
        tokens = info.get("autocomplete", "").split()
        if info.get("type") in PROTECTED_TYPES:
            return False, f"protected_field:{info['type']}"
        if info.get("inputmode") in ("email", "tel") or "@" in info.get("placeholder", ""):
            return False, "personal_field"
        if any(t.startswith("cc-") for t in tokens):
            return False, "payment_field"
        personal = next((t for t in tokens if PERSONAL_TOKENS.match(t)), None)
        if personal:
            return False, f"personal_field:{personal}"
        idents = [re.sub(r"[_\[\].-]+", " ", str(info.get(k) or "")).strip() for k in ("name", "id")]
        names = " ".join(idents)
        words = " ".join((info.get("label", ""), info.get("placeholder", ""), info.get("aria", ""),
                          str(action.get("label") or "")))
        if PAYMENT_NAMES.search(names) or self._hit("payment_field", words):
            return False, "payment_field"
        # A search box is recognised by its markup; a label that merely says "search" ("Cerca il tuo ordine") does
        # not outweigh personal words, nor does weak markup (a name such as "k", a search form action) whose label
        # does not say "search" ("Il tuo nome"); personal field names always win.
        strong_search = info.get("type") == "search" or info.get("role") == "searchbox" or bool(info.get("in_search"))
        weak_search = bool(SEARCH_NAMES.match(info.get("name", ""))) or self._hit("search", info.get("form_action", ""))
        search_box = strong_search or weak_search
        searchy = strong_search or (weak_search and self._hit("search", words))
        personal_name = any(PERSONAL_NAMES.search(n) for n in idents if n)
        if personal_name or (self._hit("personal_field", words) and not searchy) or CONTACT_WORDS.search(words):
            return False, "personal_field"  # "Cerca il tuo ordine (email)" asks for an address, whatever its markup
        if self._hit("newsletter", " ".join((words, names, info.get("form_action", ""), info.get("form_text", "")))):
            return False, "personal_field:newsletter"
        if info.get("form_password") or info.get("form_payment"):
            return False, "account_or_payment_form"
        if search_box or self._hit("search", words):
            return True, None
        numeric = info.get("type") == "number" or info.get("inputmode") == "numeric" or info.get("role") == "spinbutton"
        if numeric and (self._hit("quantity", words) or self._hit("quantity", names)):
            return True, None
        if self._hit("coupon", words) or self._hit("coupon", names):
            return True, None
        return False, "field_not_allowed"
