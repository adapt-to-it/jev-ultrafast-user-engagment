// Passo Svelto fixture shop: catalog, consent banner, search suggestions, cart in localStorage.
// "?dark=1" (or <body data-dark="1">) switches every page to its dark-pattern variant and keeps the flag on links.
(() => {
  const CATALOG = [
    [1, 'Scarpa da corsa Aurora', 49.90, 69.90, 59.90, 4.5, 128, 'a'], [2, 'Scarpa da trail Vetta', 89.00, null, null, 4.3, 64, 'b'],
    [3, 'Scarpa da corsa Brezza', 39.90, null, null, 4.1, 212, 'c'], [4, 'Scarpa da gara Lampo', 129.00, 149.00, 139.00, 4.7, 41, 'd'],
    [5, 'Scarpa da corsa Nuvola', 59.90, null, null, 4.4, 98, 'a'], [6, 'Scarpa da trail Roccia', 99.00, null, null, 4.2, 37, 'b'],
    [7, 'Scarpa da corsa Onda', 44.90, null, null, 4.0, 155, 'c'], [8, 'Scarpa da corsa Scia', 74.90, 89.90, 79.90, 4.6, 73, 'd'],
    [9, 'Scarpa da camminata Sentiero', 34.90, null, null, 3.9, 51, 'a'], [10, 'Scarpa da corsa Fiamma', 64.90, null, null, 4.4, 88, 'b'],
    [11, 'Scarpa da corsa Eco', 54.90, null, null, 4.3, 120, 'c'], [12, 'Scarpa da trail Cresta', 109.00, null, null, 4.5, 29, 'd'],
    [13, 'Scarpa da corsa Alba', 47.50, null, null, 4.2, 66, 'a'], [14, 'Scarpa da corsa Tramonto', 69.00, null, null, 4.1, 47, 'b'],
    [15, 'Scarpa da corsa Maestrale', 84.90, 99.90, 89.90, 4.6, 102, 'c'], [16, 'Scarpa da corsa Scirocco', 79.00, null, null, 4.3, 58, 'd'],
    [17, 'Scarpa da corsa Libeccio', 42.00, null, null, 4.0, 91, 'a'], [18, 'Scarpa da corsa Grecale', 58.00, null, null, 4.4, 77, 'b'],
    [19, 'Scarpa da trail Bosco', 94.90, null, null, 4.5, 34, 'c'], [20, 'Scarpa da corsa Pista', 119.00, null, null, 4.8, 22, 'd'],
    [21, 'Scarpa da corsa Riva', 36.90, 45.90, 39.90, 4.1, 140, 'a'], [22, 'Scarpa da corsa Delta', 66.60, null, null, 4.2, 53, 'b'],
    [23, 'Scarpa da corsa Faro', 52.90, null, null, 4.3, 61, 'c'], [24, 'Scarpa da corsa Porto', 88.00, null, null, 4.4, 44, 'd'],
  ].map(([id, name, price, old, lowest, rating, reviews, img]) => ({id, name, price, old, lowest, rating, reviews, img,
    cat: name.includes('trail') ? 'Trail running' : 'Scarpe da corsa'}));
  const params = new URLSearchParams(location.search);
  const DARK = params.has('dark') || document.body.dataset.dark === '1';
  const PAGE = document.body.dataset.page || '';
  const store = {
    get(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch (e) { return fallback; } },
    set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* storage disabled */ } },
  };
  const euro = v => v.toFixed(2).replace('.', ',') + ' €';
  const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const product = id => CATALOG.find(p => p.id === id) || CATALOG[0];
  const link = href => {
    if (!DARK) return href;
    const u = new URL(href, location.href);
    if (u.origin !== location.origin || !/\.html$/.test(u.pathname)) return href;
    u.pathname = u.pathname.replace(/\/product\.html$/, '/product-dark.html');
    u.searchParams.set('dark', '1');
    return u.pathname.split('/').pop() + u.search + u.hash;
  };
  if (DARK) for (const a of document.querySelectorAll('a[href]')) a.setAttribute('href', link(a.getAttribute('href')));
  if (DARK) for (const f of document.querySelectorAll('form[action]')) {
    const hidden = document.createElement('input');
    Object.assign(hidden, {type: 'hidden', name: 'dark', value: '1'});
    f.append(hidden);
  }

  // ------------------------------------------------------------- cart storage and header badge
  const cart = () => store.get('ps-cart', []);
  const saveCart = items => { store.set('ps-cart', items); badge(); };
  const badge = () => {
    const n = cart().reduce((s, i) => s + i.qty, 0);
    document.querySelectorAll('[data-cart-count]').forEach(e => { e.textContent = String(n); });
  };
  badge();

  // ------------------------------------------------------------- search suggestions (role=listbox / role=option)
  const q = document.querySelector('#q');
  if (q) {
    const list = document.createElement('ul');
    Object.assign(list, {id: 'q-suggestions', hidden: true});
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', 'Suggerimenti di ricerca');
    q.after(list);
    q.setAttribute('role', 'combobox');
    q.setAttribute('aria-controls', 'q-suggestions');
    q.setAttribute('aria-autocomplete', 'list');
    q.setAttribute('aria-expanded', 'false');
    q.addEventListener('input', () => {
      const v = q.value.trim().toLowerCase();
      const hits = v.length >= 2 ? CATALOG.filter(p => (p.name + ' ' + p.cat).toLowerCase().split(/\s+/).some(w => w.startsWith(v))
        || (p.name + ' ' + p.cat).toLowerCase().includes(v)).slice(0, 5) : [];
      list.innerHTML = hits.map(p => `<li role="option"><a href="${link('product.html?id=' + p.id)}">${esc(p.name)}</a></li>`).join('');
      list.hidden = !hits.length;
      q.setAttribute('aria-expanded', String(!!hits.length));
    });
  }

  // ------------------------------------------------------------- consent: symmetric bar (clean), accept-only modal (dark)
  const consent = () => {
    if (store.get('ps-consent', null)) return;
    const done = choice => { store.set('ps-consent', {choice, at: Date.now()}); document.querySelectorAll('[data-consent-ui]').forEach(e => e.remove()); };
    const manage = box => {
      box.innerHTML = `<h2 id="consent-title">Preferenze cookie</h2>
        <label class="addon"><input type="checkbox" checked disabled> Cookie tecnici (sempre attivi)</label>
        <label class="addon"><input type="checkbox" ${DARK ? 'checked' : ''} data-purpose="stats"> Statistiche anonime</label>
        <label class="addon"><input type="checkbox" ${DARK ? 'checked' : ''} data-purpose="ads"> Pubblicità personalizzata</label>
        <div class="actions"><button class="btn btn-primary" data-choice="custom">Salva preferenze</button>
        <button class="btn ${DARK ? 'tiny' : 'btn-primary'}" data-choice="none">Rifiuta tutti</button></div>`;
    };
    if (!DARK) {
      const bar = document.createElement('section');
      bar.className = 'consent';
      bar.dataset.consentUi = '';
      bar.setAttribute('aria-label', 'Consenso ai cookie');
      bar.innerHTML = `<p><strong>Rispettiamo la tua privacy.</strong> Usiamo cookie tecnici e, solo con il tuo consenso, cookie di analisi e profilazione.</p>
        <div class="actions"><button class="btn btn-primary" data-choice="all">Accetta tutti</button>
        <button class="btn btn-primary" data-choice="none">Rifiuta tutti</button>
        <button class="btn btn-secondary" data-choice="manage">Personalizza</button></div>`;
      document.body.append(bar);
      bar.addEventListener('click', e => {
        const choice = e.target.closest('[data-choice]')?.dataset.choice;
        if (choice === 'manage') manage(bar); else if (choice) done(choice);
      });
    } else {
      const backdrop = document.createElement('div');
      backdrop.className = 'modal-backdrop';
      backdrop.dataset.consentUi = '';
      const box = document.createElement('div');
      box.className = 'modal';
      box.dataset.consentUi = '';
      box.setAttribute('role', 'dialog');
      box.setAttribute('aria-modal', 'true');
      box.setAttribute('aria-labelledby', 'consent-title');
      box.innerHTML = `<h2 id="consent-title">La tua esperienza, su misura</h2>
        <p>Usiamo i cookie per offrirti offerte personalizzate e pubblicità su misura. Continuando accetti l'uso dei cookie.</p>
        <button class="btn btn-loud btn-block" data-choice="all">Accetta e continua</button>
        <button class="tiny" data-choice="manage">Personalizza</button>`;
      document.body.append(backdrop, box);
      box.addEventListener('click', e => {
        const choice = e.target.closest('[data-choice]')?.dataset.choice;
        if (choice === 'manage') manage(box); else if (choice) done(choice);
      });
    }
  };
  consent();

  // ------------------------------------------------------------- dark extras: nagging newsletter modal
  const newsletter = () => setTimeout(() => {
    const backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop';
    const box = document.createElement('div');
    box.className = 'modal';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-label', 'Newsletter');
    box.innerHTML = `<button class="tiny" aria-label="Chiudi" data-close style="float:right">×</button>
      <h2>Iscriviti alla newsletter e ottieni il 10% di sconto sul tuo primo ordine</h2>
      <label>Email <input type="email" name="newsletter-email" autocomplete="email"></label>
      <button class="btn btn-loud btn-block" data-close>Iscriviti e risparmia</button>
      <button class="tiny" data-close>No grazie, preferisco pagare di più</button>`;
    document.body.append(backdrop, box);
    box.addEventListener('click', e => { if (e.target.closest('[data-close]')) { backdrop.remove(); box.remove(); } });
  }, 800);
  if (DARK && ['product', 'category'].includes(PAGE)) newsletter();

  // ------------------------------------------------------------- product page
  const addToCart = (p, size) => {
    const items = cart();
    const line = items.find(i => i.id === p.id && i.size === size);
    if (line) line.qty += 1; else items.push({id: p.id, name: p.name, price: p.price, size, qty: 1});
    saveCart(items);
  };
  if (PAGE === 'product') {
    const p = product(parseInt(params.get('id') || '1', 10));
    if (params.has('id')) {
      const set = (key, value) => document.querySelectorAll(`[data-p="${key}"]`).forEach(e => { if (value === null) e.hidden = true; else e.textContent = value; });
      set('name', p.name);
      set('crumb', p.name);
      set('price', euro(p.price));
      set('old', DARK ? euro(p.price * 2) : p.old ? euro(p.old) : null);  // dark: an invented reference price
      set('lowest', p.lowest ? `Prezzo più basso negli ultimi 30 giorni: ${euro(p.lowest)}` : null);
      set('rating', `${p.rating.toFixed(1).replace('.', ',')} su 5 (${p.reviews} recensioni)`);
      document.title = `${p.name} | Passo Svelto`;
      const img = document.querySelector('.gallery img');
      if (img) Object.assign(img, {src: `img/shoe-${p.img}.svg`, alt: `${p.name}, vista laterale`});
      const ld = document.querySelector('#product-jsonld');
      if (ld) {
        const data = JSON.parse(ld.textContent);
        data.name = p.name;
        data.sku = 'PS-' + String(p.id).padStart(4, '0');
        data.offers.price = (DARK ? p.price - 10 : p.price).toFixed(2);
        data.aggregateRating = {...data.aggregateRating, ratingValue: p.rating.toFixed(1), reviewCount: String(p.reviews)};
        ld.textContent = JSON.stringify(data);
      }
    }
    let size = document.querySelector('.sizes [aria-pressed="true"]')?.textContent.trim() || null;
    document.querySelectorAll('.sizes button').forEach(b => b.addEventListener('click', () => {
      document.querySelectorAll('.sizes button').forEach(o => o.setAttribute('aria-pressed', String(o === b)));
      size = b.textContent.trim();
    }));
    const status = document.querySelector('.added');
    document.querySelector('#add')?.addEventListener('click', () => {
      addToCart(p, size);
      if (status) status.hidden = false;
    });
    document.querySelector('#buy')?.addEventListener('click', () => {
      addToCart(p, size);
      location.href = link('cart.html');
    });
    const timer = document.querySelector('[data-countdown]');
    if (timer) {  // restarts from 15 minutes on every load
      let left = 15 * 60;
      const show = () => { timer.textContent = [0, Math.floor(left / 60), left % 60].map(v => String(v).padStart(2, '0')).join(':'); };
      show();
      setInterval(() => { left = Math.max(0, left - 1); show(); }, 1000);
    }
    if (DARK) setTimeout(() => {
      const bar = document.createElement('div');
      bar.className = 'promo-bar';
      bar.textContent = 'SALDI PAZZI: solo oggi sconti fino al 70% su tutto!';
      document.body.prepend(bar);
    }, 1200);
  }

  // ------------------------------------------------------------- cart page
  const PROTECTION = {id: 'protezione', name: 'Protezione spedizione Premium', price: 2.90, size: null, qty: 1, sneaked: true};
  const renderCart = () => {
    const root = document.querySelector('[data-cart]');
    if (!root) return;
    let items = cart();
    if (DARK && items.length && !store.get('ps-protection-removed', false)) items = [...items, PROTECTION];
    if (!items.length) {
      root.innerHTML = `<p>Il tuo carrello è vuoto.</p><p><a class="btn btn-primary" href="${link('category.html')}">Scopri le scarpe da corsa</a></p>`;
      return;
    }
    const insured = DARK ? store.get('ps-insurance', true) : false;
    const gift = !DARK && store.get('ps-gift', false);
    const subtotal = items.reduce((s, i) => s + i.price * i.qty, 0);
    const shipping = DARK ? 6.90 : subtotal >= 59 ? 0 : 4.90;
    const extras = (DARK ? 1.50 : 0) + (insured ? 4.90 : 0) + (gift ? 3.00 : 0);
    const lines = items.map((i, n) => `<div class="cart-line" data-line="${n}">
        <div><h2>${i.sneaked ? esc(i.name) : `<a href="${link('product.html?id=' + i.id)}">${esc(i.name)}</a>`}</h2>
          ${i.size ? `<p class="note">Taglia ${esc(i.size)}</p>` : ''}
          <label>Quantità <input type="number" min="1" max="9" value="${i.qty}" data-qty="${n}"></label></div>
        <div><p class="line-price">${euro(i.price * i.qty)}</p><button class="btn btn-secondary remove" data-remove="${n}">Rimuovi</button></div>
      </div>`).join('');
    root.innerHTML = `${lines}
      <label class="addon">${DARK
        ? `<input type="checkbox" data-insurance ${insured ? 'checked' : ''}> Assicurazione sull'ordine contro furto e smarrimento (+4,90 €)`
        : `<input type="checkbox" data-gift ${gift ? 'checked' : ''}> Confezione regalo (+3,00 €)`}</label>
      <div class="summary">
        <div class="row"><span>Subtotale</span><span>${euro(subtotal)}</span></div>
        <div class="row"><span>Spedizione</span><span>${shipping ? euro(shipping) : 'Gratuita'}</span></div>
        ${DARK ? `<div class="row"><span>Commissione di servizio</span><span>${euro(1.50)}</span></div>` : ''}
        ${insured ? `<div class="row"><span>Assicurazione sull'ordine</span><span>${euro(4.90)}</span></div>` : ''}
        ${gift ? `<div class="row"><span>Confezione regalo</span><span>${euro(3.00)}</span></div>` : ''}
        <div class="row total"><span>Totale (IVA inclusa)</span><span>${euro(subtotal + shipping + extras)}</span></div>
      </div>
      ${DARK ? '' : `<p>Consegna stimata in 2-4 giorni lavorativi. Spedizione gratuita per ordini sopra i 59 €.</p>
      <p>Puoi completare l'acquisto anche come ospite, senza registrazione. <a href="checkout.html?guest=1">Continua come ospite</a></p>`}
      <p><a class="btn btn-primary btn-block" href="${link('checkout.html')}">Procedi al checkout</a></p>`;
  };
  if (PAGE === 'cart') {
    renderCart();
    document.addEventListener('change', e => {
      const t = e.target;
      if (t.matches('[data-qty]')) {
        const items = cart(), i = +t.dataset.qty;
        if (items[i]) { items[i].qty = Math.max(1, Math.min(9, parseInt(t.value, 10) || 1)); saveCart(items); }
      } else if (t.matches('[data-insurance]')) store.set('ps-insurance', t.checked);
      else if (t.matches('[data-gift]')) store.set('ps-gift', t.checked);
      renderCart();
    });
    document.addEventListener('click', e => {
      const t = e.target.closest('[data-remove]');
      if (!t) return;
      const items = cart(), i = +t.dataset.remove;
      if (i >= items.length) store.set('ps-protection-removed', true); else { items.splice(i, 1); saveCart(items); }
      renderCart();
    });
  }

  // ------------------------------------------------------------- checkout page (never submitted by tests)
  if (PAGE === 'checkout') {
    if (params.has('guest')) document.querySelectorAll('[data-account]').forEach(e => e.remove());
    const box = document.querySelector('[data-summary]');
    const items = cart();
    if (box && items.length) {
      const subtotal = items.reduce((s, i) => s + i.price * i.qty, 0), shipping = subtotal >= 59 ? 0 : 4.90;
      box.innerHTML = items.map(i => `<div class="row"><span>${esc(i.name)} × ${i.qty}</span><span>${euro(i.price * i.qty)}</span></div>`).join('') +
        `<div class="row"><span>Spedizione</span><span>${shipping ? euro(shipping) : 'Gratuita'}</span></div>
         <div class="row total"><span>Totale (IVA inclusa)</span><span>${euro(subtotal + shipping)}</span></div>`;
    }
  }
})();
