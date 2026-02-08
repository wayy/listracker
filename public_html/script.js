const tg = window.Telegram.WebApp;
const chatId = tg.initDataUnsafe?.user?.id;

const categoriesEl = document.getElementById('categories');
const itemsListEl = document.getElementById('items-list');
const pageNumEl = document.getElementById('page-num');
const modalEl = document.getElementById('modal');
const modalNameEl = document.getElementById('m-name');
const modalPriceEl = document.getElementById('m-price');
const trackBtnEl = document.getElementById('track-btn');
const emptyStateEl = document.getElementById('empty-state');
const errorEl = document.getElementById('error');
const prevBtnEl = document.getElementById('prev-btn');
const nextBtnEl = document.getElementById('next-btn');
const statusPillEl = document.getElementById('status-pill');

let currentCat = '';
let page = 0;
let selected = '';
let isTracked = false;

function setStatus(text, isOk) {
    statusPillEl.textContent = text;
    statusPillEl.classList.toggle('status-pill--warn', !isOk);
}

function setError(message) {
    if (!message) {
        errorEl.hidden = true;
        errorEl.textContent = '';
        return;
    }
    errorEl.hidden = false;
    errorEl.textContent = message;
}

function setEmptyState(visible, message) {
    emptyStateEl.hidden = !visible;
    if (message) emptyStateEl.textContent = message;
}

async function loadCats() {
    if (!chatId) {
        tg.showAlert('Не удалось определить Telegram ID.');
        setStatus('Ошибка', false);
        return;
    }

    setStatus('Синхронизация...', true);
    categoriesEl.innerHTML = '<div class="loading">⏳ Загружаю категории...</div>';
    setError('');
    setEmptyState(false);

    const res = await fetch(`/api/categories?chat_id=${chatId}`);
    const data = await res.json();

    if (!res.ok) {
        let message = 'Не удалось загрузить инвентарь. Попробуйте позже.';
        if (data?.error === 'PROFILE_PRIVATE') {
            message = 'Профиль закрыт. Откройте профиль в Steam.';
        } else if (data?.error === 'INVENTORY_PRIVATE') {
            message = 'Инвентарь скрыт. Откройте инвентарь в Steam.';
        } else if (data?.error === 'STEAM_ID_MISSING') {
            message = 'Сначала отправьте боту ссылку на Steam профиль.';
        }
        setStatus('Ошибка', false);
        categoriesEl.innerHTML = '';
        setError(message);
        tg.showAlert(message);
        return;
    }

    setStatus('● Онлайн', true);
    categoriesEl.innerHTML = data.map((cat) => `<button onclick="setCat(this, '${cat.replace(/'/g, "\\'")}')">${cat}</button>`).join('');

    const firstBtn = categoriesEl.querySelector('button');
    if (firstBtn) {
        setCat(firstBtn, firstBtn.textContent);
    } else {
        setEmptyState(true, 'Инвентарь пуст.');
    }
}

async function setCat(btn, cat) {
    document.querySelectorAll('.tabs button').forEach((b) => b.classList.remove('active'));
    btn.classList.add('active');
    currentCat = cat;
    page = 0;
    await loadItems();
}

async function loadItems() {
    if (!currentCat) return;
    itemsListEl.innerHTML = '<div class="loading">⏳ Загружаю предметы...</div>';
    setEmptyState(false);
    prevBtnEl.disabled = page === 0;

    const res = await fetch(`/api/items?chat_id=${chatId}&category=${encodeURIComponent(currentCat)}&page=${page}`);
    const items = await res.json();

    pageNumEl.innerText = `Стр. ${page + 1}`;

    if (!res.ok) {
        setEmptyState(true, 'Не удалось загрузить предметы.');
        return;
    }

    if (!items.length) {
        itemsListEl.innerHTML = '';
        setEmptyState(true, page === 0 ? 'Нет предметов в этой категории.' : 'Нет предметов на этой странице.');
        return;
    }

    itemsListEl.innerHTML = items.map((item) => `
        <div class="card">
            <div class="card-title">${item.name}</div>
            <div class="amount">x${item.amount}</div>
            <button onclick="showPrice('${item.name.replace(/'/g, "\\'")}')">Цена</button>
        </div>
    `).join('');
}

async function showPrice(name) {
    selected = name;
    modalEl.hidden = false;
    modalNameEl.innerText = name;
    modalPriceEl.innerText = '⏳ Загрузка...';
    trackBtnEl.disabled = true;

    const res = await fetch(`/api/get-price?name=${encodeURIComponent(name)}&chat_id=${chatId}`);
    const data = await res.json();

    if (!res.ok) {
        modalPriceEl.innerText = 'Цена недоступна.';
        trackBtnEl.disabled = false;
        return;
    }

    modalPriceEl.innerText = data.priceStr || 'Цена недоступна.';
    isTracked = Boolean(data.tracking);
    trackBtnEl.textContent = isTracked ? 'Перестать отслеживать' : 'Отслеживать';
    trackBtnEl.classList.toggle('btn-track--stop', isTracked);
    trackBtnEl.disabled = false;
}

async function toggleTrack() {
    if (!selected) return;
    trackBtnEl.disabled = true;
    const endpoint = isTracked ? '/api/untrack' : '/api/track';
    const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: chatId, name: selected })
    });

    if (res.ok) {
        isTracked = !isTracked;
        trackBtnEl.textContent = isTracked ? 'Перестать отслеживать' : 'Отслеживать';
        trackBtnEl.classList.toggle('btn-track--stop', isTracked);
        tg.showAlert(isTracked ? 'Отслеживание включено!' : 'Отслеживание остановлено.');
        if (!isTracked) closeModal();
    } else {
        const data = await res.json();
        tg.showAlert(data?.message || 'Не удалось изменить отслеживание.');
    }
    trackBtnEl.disabled = false;
}

function closeModal() {
    modalEl.hidden = true;
}

function nextPage() {
    page += 1;
    loadItems();
}

function prevPage() {
    if (page === 0) return;
    page -= 1;
    loadItems();
}

tg.ready();
tg.expand();
loadCats();
