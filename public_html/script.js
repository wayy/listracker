
const tg = window.Telegram.WebApp;
tg.expand();

async function loadInventory() {
    const chatId = tg.initDataUnsafe.user.id;
    // Запрос к твоему новому API
    const response = await fetch(`/api/inventory?chat_id=${chatId}`);
    const data = await response.json();
    
    const container = document.getElementById('inventory');
    container.innerHTML = data.map(item => `
        <div class="item-card">
            <span>${item.name}</span>
            <b>x${item.amount}</b>
        </div>
    `).join('');
}

loadInventory();
