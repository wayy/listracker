// ВАЖНО: Укажите здесь URL вашего бэкенда (например, через ngrok или реальный домен)
// Если тестируете локально, Mini App не сможет достучаться до localhost без туннеля (из-за HTTPS на GitHub Pages)
const API_BASE_URL = 'https://YOUR_DOMAIN_OR_NGROK.com'; 

const tg = window.Telegram.WebApp;
tg.expand();

let inventory = [];
let categories = {};
let currentCategory = null;
let currentPage = 1;
const ITEMS_PER_PAGE = 10;
let userTgId = null;

// Инициализация
document.addEventListener('DOMContentLoaded', async () => {
    // Получаем ID пользователя из initData (безопаснее через валидацию на бэке, но для ТЗ берем так)
    if (tg.initDataUnsafe && tg.initDataUnsafe.user) {
        userTgId = tg.initDataUnsafe.user.id;
        loadInventory();
    } else {
        document.getElementById('loader').innerHTML = '<p>Ошибка: Запустите через Telegram</p>';
    }
});

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
        document.getElementById('loader').innerHTML = `<p style="color:red">Ошибка: ${e.message}</p>`;
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
        div.innerHTML = `
            <img src="${item.image}" alt="${item.name}">
            <div class="card-title">${item.name}</div>
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
    btn.textContent = 'Отслеживать';
    btn.onclick = null; // Сброс

    // Запрос цены
    try {
        const res = await fetch(`${API_BASE_URL}/api/price?name=${encodeURIComponent(item.market_hash_name)}`);
        const data = await res.json();
        
        if (data.price) {
            priceEl.textContent = `Цена: ${data.text}`;
            btn.disabled = false;
            
            // Логика отслеживания
            btn.onclick = () => trackItem(item, data.price, data.text);
        } else {
            priceEl.textContent = 'Не удалось получить цену';
        }
    } catch (e) {
        priceEl.textContent = 'Ошибка сети';
    }
}

async function trackItem(item, price, priceText) {
    const btn = document.getElementById('track-btn');
    btn.textContent = 'Сохранение...';
    
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
        
        if (result.status === 'success') {
            tg.showAlert(`Отслеживание начато!\nБазовая цена: ${priceText}`);
            closeModal();
        } else if (result.status === 'already_tracked') {
            tg.showAlert('Вы уже отслеживаете этот предмет.');
        } else {
            tg.showAlert('Ошибка сервера.');
        }
    } catch (e) {
        tg.showAlert('Ошибка связи с сервером.');
    }
    btn.textContent = 'Отслеживать';
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
