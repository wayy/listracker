// ВАЖНО: Укажите здесь URL вашего бэкенда (например, через ngrok или реальный домен)
// Если тестируете локально, Mini App не сможет достучаться до localhost без туннеля (из-за HTTPS на GitHub Pages)
const API_BASE_URL = 'https://prxnone.bothost.ru';

const tg = window.Telegram.WebApp;
tg.expand();

let inventory = [];
let categories = {};
let currentCategory = null;
let currentPage = 1;
const ITEMS_PER_PAGE = 10;
let userTgId = null;
let trackedItems = new Set(); // Set of market_hash_names

// Инициализация
document.addEventListener('DOMContentLoaded', async () => {
    // Получаем ID пользователя из initData
    if (tg.initDataUnsafe && tg.initDataUnsafe.user) {
        userTgId = tg.initDataUnsafe.user.id;
        document.getElementById('loader').innerHTML = '<div class="spinner"></div><p>Загрузка данных...</p><br><small>ID: ' + userTgId + '</small>';
        await loadTrackedItems();
        loadInventory();
    } else {
        // Fallback for testing without Telegram environment
        const urlParams = new URLSearchParams(window.location.search);
        const debugTgId = urlParams.get('tg_id');
        if (debugTgId) {
            userTgId = debugTgId;
            document.getElementById('loader').innerHTML = '<div class="spinner"></div><p>Режим отладки...</p><br><small>ID: ' + userTgId + '</small>';
            await loadTrackedItems();
            loadInventory();
        } else {
            document.getElementById('loader').innerHTML = '<p style="color:red">Ошибка: Не удалось определить пользователя.<br>Запустите через Telegram.</p>';
        }
    }
});

async function loadTrackedItems() {
    try {
        const response = await fetch(`${API_BASE_URL}/api/tracked?tg_id=${userTgId}`);
        if (!response.ok) throw new Error('API Error: ' + response.status);
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
        document.getElementById('loader').innerHTML = `<p style="color:red">Ошибка загрузки инвентаря:<br>${e.message}</p><br><button onclick="location.reload()" class="action-btn">Повторить</button>`;
    }
}

// Группировка по категориям (на основе имени)
function processCategories() {
    categories = {};
    inventory.forEach(item => {
        // Логика выделения категории: берем часть до " | " или первое слово
        let catName = item.name.split(' | ')[0];
        if (catName.includes('Sticker')) catName = 'Stickers';
        if (catName.includes('Case')) catName = 'Cases';
        if (catName.includes('Graffiti')) catName = 'Graffiti';

        if (!categories[catName]) categories[catName] = [];
        categories[catName].push(item);
    });
}

function renderCategories() {
    const list = document.getElementById('categories-list');
    list.innerHTML = '';

    Object.keys(categories).sort().forEach(cat => {
        const div = document.createElement('div');
        div.className = 'card';
        // Берем иконку первого предмета как иконку категории
        div.innerHTML = `
            <img src="${categories[cat][0].image}" alt="${cat}">
            <div class="card-title">${cat} (${categories[cat].length})</div>
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
        // Добавляем маркер если отслеживается
        const isTracked = trackedItems.has(item.market_hash_name);
        div.innerHTML = `
            <img src="${item.image}" alt="${item.name}">
            <div class="card-title">${item.name} ${isTracked ? '👁️' : ''}</div>
        `;
        div.onclick = () => openItemModal(item);
        list.appendChild(div);
    });

    // Пагинация
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

    // Сброс обработчиков (чтобы не дублировались)
    let newBtn = btn.cloneNode(true);
    btn.parentNode.replaceChild(newBtn, btn);
    newBtn = document.getElementById('track-btn'); // Refresh reference

    const isTracked = trackedItems.has(item.market_hash_name);

    // Начальное состояние кнопки
    updateModalButton(newBtn, isTracked, item, null, null);

    // Запрос цены
    try {
        const res = await fetch(`${API_BASE_URL}/api/price?name=${encodeURIComponent(item.market_hash_name)}`);
        const data = await res.json();

        if (data.price) {
            priceEl.textContent = `Цена: ${data.text}`;
            newBtn.disabled = false;

            // Обновляем кнопку с полученной ценой
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
                currency: priceText.replace(/[\d.,\s]/g, '') // Пытаемся вычленить валюту
            })
        });
        const result = await res.json();

        if (result.status === 'success' || result.status === 'already_tracked') {
            tg.showAlert(`Отслеживание начато!\nБазовая цена: ${priceText}`);
            trackedItems.add(item.market_hash_name);
            closeModal();
            renderItems(); // Обновить иконки
        } else {
            tg.showAlert('Ошибка сервера.');
        }
    } catch (e) {
        tg.showAlert('Ошибка связи с сервером.');
    }
    // Кнопку не включаем, так как модалка закрывается. Если ошибка — она останется выключенной пока юзер не переоткроет, или можно разблокировать.
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
            tg.showAlert('Отслеживание остановлено.');
            trackedItems.delete(item.market_hash_name);
            closeModal();
            renderItems(); // Обновить иконки
        } else {
            tg.showAlert('Ошибка сервера.');
        }
    } catch (e) {
        tg.showAlert('Ошибка связи с сервером.');
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
