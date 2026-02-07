const tg = window.Telegram.WebApp;
const chatId = tg.initDataUnsafe.user.id;
let currentCategory = '';
let currentPage = 0;

async function loadCategories() {
    const res = await fetch(`/api/categories?chat_id=${chatId}`);
    const cats = await res.json();
    const list = document.getElementById('categories');
    list.innerHTML = cats.map(c => `<button onclick="selectCategory('${c}')">${c}</button>`).join('');
}

async function selectCategory(cat) {
    currentCategory = cat;
    currentPage = 0;
    document.getElementById('items-view').style.display = 'block';
    loadItems();
}

async function loadItems() {
    const res = await fetch(`/api/items?chat_id=${chatId}&category=${currentCategory}&page=${currentPage}`);
    const items = await res.json();
    
    const container = document.getElementById('items-list');
    container.innerHTML = items.map(i => `
        <div class="item">
            <span>${i.name} (x${i.amount})</span>
            <button onclick="checkPrice('${i.name}')">Узнать цену</button>
        </div>
    `).join('');
}

function nextPage() { currentPage++; loadItems(); }
function prevPage() { if(currentPage > 0) { currentPage--; loadItems(); } }

loadCategories();
tg.expand();
