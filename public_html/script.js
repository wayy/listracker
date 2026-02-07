const tg = window.Telegram.WebApp;
const chatId = tg.initDataUnsafe?.user?.id || 12345; // 12345 для тестов в браузере
let currentCategory = '';
let currentPage = 0;
let selectedItem = null;

tg.expand();

// Загрузка категорий при старте
async function loadCategories() {
    try {
        const res = await fetch(`/api/categories?chat_id=${chatId}`);
        const cats = await res.json();
        const container = document.getElementById('categories');
        
        container.innerHTML = cats.map(c => 
            `<button onclick="selectCategory(this, '${c}')">${c}</button>`
        ).join('');
    } catch (e) { console.error("Ошибка категорий", e); }
}

async function selectCategory(btn, cat) {
    // Подсветка активной вкладки
    document.querySelectorAll('.tabs button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');

    currentCategory = cat;
    currentPage = 0;
    loadItems();
}

async function loadItems() {
    const list = document.getElementById('items-list');
    list.innerHTML = '<p>Загрузка предметов...</p>';

    const res = await fetch(`/api/items?chat_id=${chatId}&category=${encodeURIComponent(currentCategory)}&page=${currentPage}`);
    const items = await res.json();
    
    document.getElementById('page-num').innerText = `Стр. ${currentPage + 1}`;

    list.innerHTML = items.map(i => `
        <div class="item-card">
            <div class="item-name">${i.name}</div>
            <div class="item-amount">Кол-во: ${i.amount}</div>
            <button class="btn-view" onclick="openPriceModal('${i.name}')">Цена</button>
        </div>
    `).join('');
}

function nextPage() { currentPage++; loadItems(); }
function prevPage() { if (currentPage > 0) { currentPage--; loadItems(); } }

// Работа с ценой
async function openPriceModal(name) {
    selectedItem = name;
    document.getElementById('price-modal').style.display = 'flex';
    document.getElementById('modal-item-name').innerText = name;
    document.getElementById('modal-price').innerText = 'Загрузка...';

    // В app.js нужно создать этот эндпоинт (вызов функции getSteamPrice)
    const res = await fetch(`/api/get-price?name=${encodeURIComponent(name)}`);
    const data = await res.json();
    document.getElementById('modal-price').innerText = `Текущая цена: ${data.priceStr}`;
}

async function trackItem() {
    // Отправляем запрос на бэкенд для записи в таблицу tracking
    const res = await fetch('/api/track', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ chat_id: chatId, name: selectedItem })
    });
    
    if (res.ok) {
        tg.showAlert('Предмет добавлен в отслеживание!');
        closeModal();
    }
}

function closeModal() {
    document.getElementById('price-modal').style.display = 'none';
}

loadCategories();
