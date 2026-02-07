
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
const PORT = process.env.PORT || 8080;

app.use(cors());
app.use(express.static(path.join(__dirname, 'public_html')));

let db;

// === ИНИЦИАЛИЗАЦИЯ БД (Твои таблицы) ===
async function initDb() {
    db = await open({
        filename: './inventory.db',
        driver: sqlite3.Database
    });
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

// === ЛОГИКА STEAM (Аналог твоего get_steam_price) ===
async function getSteamPrice(itemName) {
    const url = `https://steamcommunity.com/market/priceoverview/`;
    try {
        const response = await axios.get(url, {
            params: { appid: 730, currency: 5, market_hash_name: itemName },
            headers: { 'User-Agent': 'Mozilla/5.0' }
        });
        if (response.data && response.data.lowest_price) {
            const priceRaw = response.data.lowest_price;
            const priceNum = parseFloat(priceRaw.replace(/[^\d.,]/g, '').replace(',', '.'));
            return { priceNum, priceStr: priceRaw };
        }
        return { priceNum: null, priceStr: "Нет данных" };
    } catch (e) {
        return { priceNum: null, priceStr: "Ошибка API" };
    }
}

// === API ДЛЯ MINI APP (Твой эндпоинт) ===
app.get('/api/inventory', async (req, res) => {
    const { chat_id } = req.query;
    if (!chat_id) return res.status(400).json({ error: 'no_id' });

    const items = await db.all(`
        SELECT i.name, ui.amount, i.category 
        FROM items i JOIN user_items ui ON i.id = ui.item_id 
        WHERE ui.chat_id = ?
    `, [chat_id]);
    
    res.json(items);
});

// === ТЕЛЕГРАМ БОТ (Команды) ===
bot.start((ctx) => {
    ctx.reply('Привет! Открой приложение для управления инвентарем:', 
        Markup.keyboard([
            Markup.button.webApp('📦 Открыть Трекер', `https://твой-домен.ru/index.html`)
        ]).resize()
    );
});

// === МОНИТОРИНГ ЦЕН (Твой monitor_prices_task) ===
async function monitorPrices() {
    console.log("Запуск мониторинга...");
    const tracks = await db.all("SELECT * FROM tracking");
    for (const track of tracks) {
        const { priceNum, priceStr } = await getSteamPrice(track.item_name);
        if (priceNum && priceNum > track.last_price) {
            await bot.telegram.sendMessage(track.chat_id, 
                `📈 *Цена выросла!*\n${track.item_name}\nБыло: ${track.last_price} -> Стало: ${priceStr}`, 
                { parse_mode: 'Markdown' }
            );
            await db.run("UPDATE tracking SET last_price = ? WHERE id = ?", [priceNum, track.id]);
        }
        await new Promise(r => setTimeout(r, 5000)); // Задержка 5 сек между запросами
    }
}

// Запуск всего
async function start() {
    await initDb();
    bot.launch();
    app.listen(PORT, () => console.log(`Сервер Mini App на порту ${PORT}`));
    
    // Запуск мониторинга раз в час
    setInterval(monitorPrices, 3600000);
}

start();
