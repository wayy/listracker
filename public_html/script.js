const tg = window.Telegram.WebApp;
const chatId = tg.initDataUnsafe?.user?.id;
let currentCat = '', page = 0, selected = '';

async function loadCats() {
    const res = await fetch(`/api/categories?chat_id=${chatId}`);
    const cats = await res.json();
    document.getElementById('categories').innerHTML = cats.map(c => `<button onclick="setCat(this, '${c}')">${c}</button>`).join('');
}

async function setCat(btn, cat) {
    document.querySelectorAll('.tabs button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentCat = cat; page = 0; loadItems();
}

async function loadItems() {
    const res = await fetch(`/api/items?chat_id=${chatId}&category=${encodeURIComponent(currentCat)}&page=${page}`);
    const items = await res.json();
    document.getElementById('page-num').innerText = `Стр. ${page + 1}`;
    document.getElementById('items-list').innerHTML = items.map(i => `
        <div class="card">
            <div>${i.name}</div>
            <button onclick="showPrice('${i.name}')">Цена</button>
        </div>
    `).join('');
}

async function showPrice(name) {
    selected = name;
    document.getElementById('modal').style.display = 'flex';
    document.getElementById('m-name').innerText = name;
    document.getElementById('m-price').innerText = '⏳ Загрузка...';
    const res = await fetch(`/api/get-price?name=${encodeURIComponent(name)}`);
    const data = await res.json();
    document.getElementById('m-price').innerText = data.priceStr;
}

async function trackItem() {
    const res = await fetch('/api/track', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ chat_id: chatId, name: selected })
    });
    if(res.ok) { tg.showAlert('Отслеживание включено!'); closeModal(); }
}

function nextPage() { page++; loadItems(); }
function prevPage() { if(page > 0) { page--; loadItems(); } }
function closeModal() { document.getElementById('modal').style.display = 'none'; }

loadCats();
tg.expand();
