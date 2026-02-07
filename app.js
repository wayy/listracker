require('dotenv').config();
const { Telegraf, Markup } = require('telegraf');
const express = require('express');
const path = require('path');
const sqlite3 = require('sqlite3');
const { open } = require('sqlite');
const axios = require('axios');
const cors = require('cors');

const bot = new Telegraf(process.env.BOT_TOKEN);
const app = express();
const PORT = 8080;

app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public_html')));

let db;

// --- ИНИЦИАЛИЗАЦИЯ БД ---
async function initDb() {
    db = await open({ filename: './inventory.db', driver: sqlite3.Database });
    await db.exec(`
        CREATE TABLE IF NOT EXISTS users (chat_id INTEGER PRIMARY KEY, steam_id TEXT);
        CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, category TEXT);
        CREATE TABLE IF NOT EXISTS user_items (chat_id INTEGER, item_id INTEGER, amount INTEGER, PRIMARY KEY (chat_id, item_id));
        CREATE TABLE IF NOT EXISTS tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            chat_id INTEGER, 
            item_name TEXT, 
            last_price REAL, 
            UNIQUE(chat_id, item_name)
        );
    `);
}

// --- ЛОГИКА STEAM (РЕЗОЛВ ID И ПАРСИНГ) ---
async function resolveSteamId(text) {
    const cleanText = text.replace(/\/$/, "");
    if (/^\d{17}$/.test(cleanText)) return cleanText;
    
    const profileMatch = cleanText.match(/profiles\/(\d+)/);
    if (profileMatch) return profileMatch[1];
    
    const vanityMatch = cleanText.match(/id\/([^\/]+)/);
    if (vanityMatch) {
        try {
            const res = await axios.get(`https://steamcommunity.com/id/${vanityMatch[1]}/?xml=1`);
            const idMatch = res.data.match(/<steamID64>(\d+)<\/steamID64>/);
            return idMatch ? idMatch[1] : null;
        } catch (e) { return null; }
    }
    return null;
}

function getCategory(name) {
    const n = name.toLowerCase();
    if (/кейс|case|пакет|набор/.test(n)) return "📦 Кейсы";
    if (/sticker|наклейка/.test(n)) return "🎯 Наклейки";
    if (/agent|агент/.test(n)) return "👤 Агенты";
    if (/|/.test(n)) return "🔫 Оружие";
    return "🛠 Прочее";
}

async function syncInventory(chatId, steamId) {
    try {
        const url = `https://steamcommunity.com/inventory/${steamId}/730/2?l=russian&count=2000`;
        const res = await axios.get(url);
        if (!res.data || !res.data.descriptions) return false;

        const counts = {};
        res.data.descriptions.forEach(d => {
            if (d.marketable) counts[d.market_hash_name] = (counts[d.market_hash_name] || 0) + 1;
        });

        await db.run("DELETE FROM user_items WHERE chat_id = ?", [chatId]);
        for (const [name, amount] of Object.entries(counts)) {
            const cat = getCategory(name);
            await db.run("INSERT OR IGNORE INTO items (name, category) VALUES (?, ?)", [name, cat]);
            const item = await db.get("SELECT id FROM items WHERE name = ?", [name]);
            await db.run("INSERT INTO user_items (chat_id, item_id, amount) VALUES (?, ?, ?)", [chatId, item.id, amount]);
        }
        return true;
    } catch (e) { return false; }
}

// --- API ЭНДПОИНТЫ ДЛЯ MINI APP ---
app.get('/api/categories', async (req, res) => {
    const { chat_id } = req.query;
    const cats = await db.all(`
        SELECT DISTINCT i.category FROM items i 
        JOIN user_items ui ON i.id = ui.item_id WHERE ui.chat_id = ?`, [chat_id]);
    res.json(cats.map(c => c.category));
});

app.get('/api/items', async (req, res) => {
    const { chat_id, category, page = 0 } = req.query;
    const offset = page * 10;
    const items = await db.all(`
        SELECT i.id, i.name, ui.amount FROM items i 
        JOIN user_items ui ON i.id = ui.item_id 
        WHERE ui.chat_id = ? AND i.category = ?
        LIMIT 10 OFFSET ?`, [chat_id, category, offset]);
    res.json(items);
});

// --- ТЕЛЕГРАМ БОТ ---
bot.start((ctx) => ctx.reply("👋 Привет! Пришли ссылку на свой Steam профиль."));

bot.on('text', async (ctx) => {
    if (ctx.message.text.includes('steamcommunity.com')) {
        const sid = await resolveSteamId(ctx.message.text);
        if (!sid) return ctx.reply("❌ Не удалось найти Steam ID.");
        
        await ctx.reply("⏳ Синхронизирую инвентарь...");
        const success = await syncInventory(ctx.chat.id, sid);
        
        if (success) {
            await db.run("INSERT OR REPLACE INTO users (chat_id, steam_id) VALUES (?, ?)", [ctx.chat.id, sid]);
            ctx.reply("✅ Готово!", Markup.keyboard([
                [Markup.button.webApp("📦 Открыть инвентарь", `https://${process.env.DOMAIN}/index.html`)]
            ]).resize());
        } else {
            ctx.reply("❌ Ошибка. Проверь, чтобы инвентарь был открыт.");
        }
    }
});

// --- МОНИТОРИНГ (РАЗ В ЧАС) ---
setInterval(async () => {
    const tracks = await db.all("SELECT * FROM tracking");
    for (const t of tracks) {
        // Здесь твоя логика getSteamPrice из предыдущего ответа
        // Если цена > t.last_price -> bot.telegram.sendMessage(t.chat_id, ...)
    }
}, 3600000);

initDb().then(() => {
    bot.launch();
    app.listen(PORT, () => console.log(`Server running on port ${PORT}`));
});
