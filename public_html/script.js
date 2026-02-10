// ВАЖНО: Укажите здесь URL вашего бэкенда (например, через ngrok или реальный домен)
const API_BASE_URL = 'https://prxnone.bothost.ru';

// Переменные
let inventory = [];
let categories = {};
let currentCategory = null;
let currentPage = 1;
const ITEMS_PER_PAGE = 10;
let userTgId = null;
let trackedItems = new Set(); // Set of market_hash_names

// Глобальная переменная для доступа к TG API
window.tg = null;

// Инициализация
document.addEventListener('DOMContentLoaded', async () => {
    try {
        // Попытка инициализации Telegram WebApp
        if (window.Telegram && window.Telegram.WebApp) {
            window.tg = window.Telegram.WebApp;
            window.tg.expand();
        }

        // Получаем ID пользователя из initData
        if (window.tg) {
            const unsafe = window.tg.initDataUnsafe;

            // Расширенная диагностика если user пустой
            const debugInfo = `
                <div style="font-size:10px; color: #888; text-align:left; margin-top:10px; border-top:1px solid #444; padding-top:5px;">
                Platform: ${window.tg.platform}<br>
                Version: ${window.tg.version}<br>
                InitData: ${window.tg.initData ? 'Yes (Length: ' + window.tg.initData.length + ')' : 'No'}<br>
                User: ${unsafe && unsafe.user ? 'Found (ID: ' + unsafe.user.id + ')' : 'NOT FOUND'}
                </div>
            `;

            if (unsafe && unsafe.user) {
                userTgId = unsafe.user.id;
                document.getElementById('loader').innerHTML = `
                    <div class="spinner"></div>
                    <p>Загрузка инвентаря...</p>
                    <small style="color:#aaa">ID: ${userTgId}</small>
                `;

                await loadTrackedItems();
                loadInventory();
            } else {
                // Если user не найден, пробуем поискать в URL (на случай если мы прокинули его вручную)
                const urlParams = new URLSearchParams(window.location.search);
                const queryTgId = urlParams.get('tg_id');

                if (queryTgId) {
                    userTgId = queryTgId;
                    await loadTrackedItems();
                    loadInventory();
                } else {
                    document.getElementById('loader').innerHTML = `
                        <p style="color:#ff6b6b; font-weight:bold;">Ошибка: Пользователь не определен</p>
                        <p style="font-size:12px">Пожалуйста, откройте приложение через кнопку меню в боте.</p>
                        ${debugInfo}
                    `;
                }
            }
        } else {
            // Режим разработки в браузере
            const urlParams = new URLSearchParams(window.location.search);
            const debugTgId = urlParams.get('tg_id');
            if (debugTgId) {
                userTgId = debugTgId;
                document.getElementById('loader').innerHTML = '<div class="spinner"></div><p>Режим разработки...</p>';
                await loadTrackedItems();
                loadInventory();
            } else {
                document.getElementById('loader').innerHTML = '<p style="color:#ff6b6b">Ошибка: Telegram WebApp не найден.</p>';
            }
        }
    } catch (e) {
        console.error('Initial error:', e);
        document.getElementById('loader').innerHTML = `<p style="color:red">Ошибка инициализации:<br>${e.message}</p>`;
    }
});

async function loadTrackedItems() {
    try {
        const response = await fetch(`${API_BASE_URL}/api/tracked?tg_id=${userTgId}`);
        if (!response.ok) throw new Error('Status: ' + response.status);
        const data = await response.json();
        if (data.tracked) {
            trackedItems = new Set(data.tracked);
        }
    } catch (e) {
        console.error("Failed to load tracked items:", e);
    }
}

async function loadInventory() {
    try {
        const response = await fetch(`${API_BASE_URL}/api/inventory?tg_id=${userTgId}`);
        const data = await response.json();

        if (data.error) throw new Error(data.error);
        if (!data.items || data.items.length === 0) throw new Error("Инвентарь пуст или скрыт");

        inventory = data.items;
        processCategories();
        renderCategories();
        switchScreen('categories-screen');
    } catch (e) {
        document.getElementById('loader').innerHTML = `
            <p style="color:#ff6b6b">Ошибка загрузки данных:<br>${e.message}</p>
            <br>
            <button onclick="location.reload()" class="action-btn">Повторить</button>
        `;
    }
}

// Группировка по крупным категориям (как на скриншоте пользователя)
function processCategories() {
    categories = {
        'Оружие': [],
        'Наклейки': [],
        'Кейсы': [],
        'Граффити': [],
        'Музыка': [],
        'Агенты': [],
        'Прочее': []
    };

    inventory.forEach(item => {
        const type = (item.type || '').toLowerCase();
        const name = (item.name || '').toLowerCase();

        if (name.includes('graffiti')) {
            categories['Граффити'].push(item);
        } else if (name.includes('sticker')) {
            categories['Наклейки'].push(item);
        } else if (name.includes('music kit')) {
            categories['Музыка'].push(item);
        } else if (type.includes('agent') || type.includes('агент')) {
            categories['Агенты'].push(item);
        } else if (type.includes('case') || type.includes('container') || type.includes('кейс') || type.includes('ящик')) {
            categories['Кейсы'].push(item);
        } else if (
            type.includes('pistol') || type.includes('rifle') || type.includes('sniper') ||
            type.includes('smg') || type.includes('shotgun') || type.includes('machinegun') ||
            type.includes('knife') || type.includes('gloves') || type.includes('оруж') ||
            type.includes('автомат') || type.includes('пистолет') || type.includes('нож')
        ) {
            categories['Оружие'].push(item);
        } else {
            categories['Прочее'].push(item);
        }
    });

    // Удаляем пустые категории
    for (const key in categories) {
        if (categories[key].length === 0) {
            delete categories[key];
        }
    }
}

// Иконки для категорий (Emoji или картинка первого предмета)
function getCategoryIcon(catName) {
    const icons = {
        'Граффити': '🎨',
        'Наклейки': '🎯',
        'Музыка': '🎵',
        'Агенты': '👤',
        'Кейсы': '📦',
        'Оружие': '🔫',
        'Прочее': '🛠️'
    };
    return icons[catName] || '📂';
}

function renderCategories() {
    const list = document.getElementById('categories-list');
    list.innerHTML = '';

    Object.keys(categories).sort().forEach(cat => {
        const div = document.createElement('div');
        div.className = 'card';
        div.innerHTML = `
            <div style="font-size: 40px; margin-bottom: 10px;">${getCategoryIcon(cat)}</div>
            <div class="card-title">${cat}</div>
            <div style="font-size: 12px; color: #888;">${categories[cat].length} поз.</div>
        `;
        div.onclick = () => openCategory(cat);
        list.appendChild(div);
    });
}

function openCategory(catName) {
    currentCategory = catName;
    currentPage = 1;
    document.getElementById('category-title').textContent = catName;
    renderItems();
    switchScreen('items-screen');
}

function renderItems() {
    const list = document.getElementById('items-list');
    list.innerHTML = '';

    const items = categories[currentCategory];
    const start = (currentPage - 1) * ITEMS_PER_PAGE;
    const end = start + ITEMS_PER_PAGE;
    const pageItems = items.slice(start, end);

    pageItems.forEach(item => {
        const div = document.createElement('div');
        div.className = 'card';
        const isTracked = trackedItems.has(item.market_hash_name);
        div.innerHTML = `
            <img src="${item.image}" alt="${item.name}">
            <div class="card-title">${item.name} ${isTracked ? '👁️' : ''}</div>
        `;
        div.onclick = () => openItemModal(item);
        list.appendChild(div);
    });

    document.getElementById('page-indicator').textContent = currentPage;
    document.getElementById('prev-page').disabled = currentPage === 1;
    document.getElementById('next-page').disabled = end >= items.length;

    document.getElementById('prev-page').onclick = () => { currentPage--; renderItems(); };
    document.getElementById('next-page').onclick = () => { currentPage++; renderItems(); };
}

async function openItemModal(item) {
    const modal = document.getElementById('item-modal');
    document.getElementById('modal-img').src = item.image;
    document.getElementById('modal-title').textContent = item.name;
    const priceEl = document.getElementById('modal-price');
    const btn = document.getElementById('track-btn');

    modal.style.display = 'flex';
    priceEl.textContent = 'Загрузка цены...';
    btn.disabled = true;

    let newBtn = btn.cloneNode(true);
    btn.parentNode.replaceChild(newBtn, btn);
    newBtn = document.getElementById('track-btn');

    const isTracked = trackedItems.has(item.market_hash_name);
    updateModalButton(newBtn, isTracked, item, null, null);

    try {
        const res = await fetch(`${API_BASE_URL}/api/price?name=${encodeURIComponent(item.market_hash_name)}`);
        const data = await res.json();

        if (data.price) {
            priceEl.textContent = `Цена: ${data.text}`;
            newBtn.disabled = false;
            updateModalButton(newBtn, isTracked, item, data.price, data.text);
        } else {
            priceEl.textContent = 'Не удалось получить цену';
        }
    } catch (e) {
        priceEl.textContent = 'Ошибка сети';
    }
}

function updateModalButton(btn, isTracked, item, price, priceText) {
    if (isTracked) {
        btn.textContent = 'Перестать отслеживать';
        btn.className = 'action-btn stop-btn';
        btn.onclick = () => untrackItem(item);
    } else {
        btn.textContent = 'Отслеживать';
        btn.className = 'action-btn';
        if (price) {
            btn.onclick = () => trackItem(item, price, priceText);
        } else {
            btn.onclick = null;
        }
    }
}

async function trackItem(item, price, priceText) {
    const btn = document.getElementById('track-btn');
    btn.textContent = 'Сохранение...';
    btn.disabled = true;

    try {
        const res = await fetch(`${API_BASE_URL}/api/track`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                tg_id: userTgId,
                name: item.market_hash_name,
                price: price,
                currency: priceText.replace(/[\d.,\s]/g, '')
            })
        });
        const result = await res.json();

        if (result.status === 'success' || result.status === 'already_tracked') {
            const msg = `Отслеживание начато!\nБазовая цена: ${priceText}`;
            if (window.tg) window.tg.showAlert(msg);
            else alert(msg);

            trackedItems.add(item.market_hash_name);
            closeModal();
            renderItems();
        } else {
            if (window.tg) window.tg.showAlert('Ошибка сервера.');
            else alert('Ошибка сервера.');
        }
    } catch (e) {
        if (window.tg) window.tg.showAlert('Ошибка связи с сервером.');
        else alert('Ошибка связи с сервером.');
    }
    if (document.getElementById('item-modal').style.display !== 'none') {
        btn.disabled = false;
        btn.textContent = 'Отслеживать';
    }
}

async function untrackItem(item) {
    const btn = document.getElementById('track-btn');
    btn.textContent = 'Удаление...';
    btn.disabled = true;

    try {
        const res = await fetch(`${API_BASE_URL}/api/untrack`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                tg_id: userTgId,
                name: item.market_hash_name
            })
        });
        const result = await res.json();

        if (result.status === 'success') {
            if (window.tg) window.tg.showAlert('Отслеживание остановлено.');
            else alert('Отслеживание остановлено.');

            trackedItems.delete(item.market_hash_name);
            closeModal();
            renderItems();
        } else {
            if (window.tg) window.tg.showAlert('Ошибка сервера.');
            else alert('Ошибка сервера.');
        }
    } catch (e) {
        if (window.tg) window.tg.showAlert('Ошибка связи с сервером.');
        else alert('Ошибка связи с сервером.');
    }

    if (document.getElementById('item-modal').style.display !== 'none') {
        btn.disabled = false;
        btn.textContent = 'Перестать отслеживать';
    }
}

function closeModal() {
    document.getElementById('item-modal').style.display = 'none';
}

function showCategories() {
    switchScreen('categories-screen');
}

function switchScreen(id) {
    document.querySelectorAll('.screen').forEach(el => el.classList.remove('active'));
    document.getElementById(id).classList.add('active');
}
