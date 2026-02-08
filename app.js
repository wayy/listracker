require('dotenv').config();
const express = require('express');
const cors = require('cors');
const axios = require('axios');
const { Telegraf, Markup } = require('telegraf');
const { open } = require('sqlite');
const sqlite3 = require('sqlite3');
const cron = require('node-cron');

const PORT = process.env.PORT || 3000;
const BOT_TOKEN = process.env.BOT_TOKEN;
const DOMAIN = process.env.DOMAIN;
const WEBAPP_URL = process.env.WEBAPP_URL || (DOMAIN ? `https://${DOMAIN}/index.html` : '');
const PRICE_CURRENCY = 5; // RUB

if (!BOT_TOKEN) {
    console.error('BOT_TOKEN is required');
    process.exit(1);
}

const app = express();
app.use(cors());
app.use(express.json());
app.use(express.static('public_html'));

const bot = new Telegraf(BOT_TOKEN);
let db;

async function initDb() {
    db = await open({ filename: 'database.db', driver: sqlite3.Database });
    await db.exec(`
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            steam_id TEXT NOT NULL,
            name TEXT
        );
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_hash_name TEXT UNIQUE,
            category TEXT
        );
        CREATE TABLE IF NOT EXISTS user_items (
            chat_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            current_price REAL,
            updated_at TEXT,
            PRIMARY KEY (chat_id, item_id)
        );
        CREATE TABLE IF NOT EXISTS tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            item_name TEXT NOT NULL,
            start_price REAL NOT NULL,
            last_price REAL NOT NULL,
            started_at TEXT NOT NULL,
            UNIQUE(chat_id, item_name)
        );
    `);
}

function normalizePrice(priceStr) {
    if (!priceStr || typeof priceStr !== 'string') return null;
    const digits = priceStr.replace(/[^0-9,\.]/g, '').replace(',', '.');
    const value = parseFloat(digits);
    return Number.isFinite(value) ? value : null;
}

function getCategory(name) {
    const n = name.toLowerCase();
    if (/knife|нож/.test(n)) return '🔪 Ножи';
    if (/glove|перчатк/.test(n)) return '🧤 Перчатки';
    if (/кейс|case|пакет|набор/.test(n)) return '📦 Кейсы';
    if (/sticker|наклейка/.test(n)) return '🎯 Наклейки';
    if (/agent|агент/.test(n)) return '👤 Агенты';
    if (n.includes('|')) return '🔫 Оружие';
    return '🛠 Прочее';
}

async function fetchProfileHtml(url) {
    const res = await axios.get(url, { timeout: 15000 });
    return res.data;
}

function isProfilePrivate(html) {
    if (!html) return false;
    return /profile_private|This profile is private|Профиль закрыт|Этот профиль приватный/i.test(html);
}

function extractSteamId(html) {
    if (!html) return null;
    const match = html.match(/"steamid"\s*:\s*"(\d+)"/i) || html.match(/g_steamID\s*=\s*"(\d+)"/i);
    return match ? match[1] : null;
}

async function resolveSteamId(link) {
    const profilesMatch = link.match(/steamcommunity\.com\/profiles\/(\d{17})/i);
    if (profilesMatch) {
        return { steamId: profilesMatch[1], customUrl: false };
    }

    const idMatch = link.match(/steamcommunity\.com\/id\/([^/?#]+)/i);
    if (!idMatch) {
        return { error: 'INVALID_LINK' };
    }

    const url = `https://steamcommunity.com/id/${idMatch[1]}`;
    const html = await fetchProfileHtml(url);
    if (isProfilePrivate(html)) {
        return { error: 'PROFILE_PRIVATE' };
    }
    const steamId = extractSteamId(html);
    if (!steamId) {
        return { error: 'INVALID_LINK' };
    }
    return { steamId, customUrl: true };
}

async function checkProfilePrivacy(steamId) {
    try {
        const html = await fetchProfileHtml(`https://steamcommunity.com/profiles/${steamId}`);
        return isProfilePrivate(html);
    } catch (error) {
        return false;
    }
}

async function fetchInventory(steamId) {
    try {
        const url = `https://steamcommunity.com/inventory/${steamId}/730/2?l=russian&count=2000`;
        const res = await axios.get(url, { timeout: 20000 });
        const data = res.data;
        if (!data || data.success === 0) {
            return { ok: false, error: 'INVENTORY_PRIVATE' };
        }
        if (!data.assets || !data.descriptions) {
            return { ok: false, error: 'INVENTORY_PRIVATE' };
        }
        return { ok: true, data };
    } catch (error) {
        return { ok: false, error: 'STEAM_UNAVAILABLE' };
    }
}

async function syncInventory(chatId, steamId) {
    const inventory = await fetchInventory(steamId);
    if (!inventory.ok) return inventory;
    const { assets, descriptions } = inventory.data;
    const descriptionMap = new Map();
    descriptions.forEach((desc) => {
        const key = `${desc.classid}_${desc.instanceid}`;
        descriptionMap.set(key, desc);
    });

    const counts = new Map();
    assets.forEach((asset) => {
        const key = `${asset.classid}_${asset.instanceid}`;
        const desc = descriptionMap.get(key);
        if (!desc || !desc.marketable) return;
        const name = desc.market_hash_name;
        counts.set(name, (counts.get(name) || 0) + 1);
    });

    await db.exec('BEGIN');
    try {
        await db.run('DELETE FROM user_items WHERE chat_id = ?', [chatId]);

        for (const [name, amount] of counts.entries()) {
            const category = getCategory(name);
            await db.run(
                'INSERT INTO items (market_hash_name, category) VALUES (?, ?) ON CONFLICT(market_hash_name) DO UPDATE SET category = excluded.category',
                [name, category]
            );
            const item = await db.get('SELECT id FROM items WHERE market_hash_name = ?', [name]);
            await db.run(
                'INSERT INTO user_items (chat_id, item_id, amount, updated_at) VALUES (?, ?, ?, datetime("now"))',
                [chatId, item.id, amount]
            );
        }

        const inventoryNames = Array.from(counts.keys());
        if (inventoryNames.length === 0) {
            await db.run('DELETE FROM tracking WHERE chat_id = ?', [chatId]);
        } else {
            const placeholders = inventoryNames.map(() => '?').join(',');
            await db.run(
                `DELETE FROM tracking WHERE chat_id = ? AND item_name NOT IN (${placeholders})`,
                [chatId, ...inventoryNames]
            );
        }

        await db.exec('COMMIT');
        return { ok: true };
    } catch (error) {
        await db.exec('ROLLBACK');
        return { ok: false, error: 'DB_ERROR' };
    }
}

async function getSteamPrice(name) {
    try {
        const res = await axios.get('https://steamcommunity.com/market/priceoverview/', {
            timeout: 15000,
            params: {
                currency: PRICE_CURRENCY,
                appid: 730,
                market_hash_name: name
            }
        });
        const data = res.data || {};
        if (!data.success) {
            return { priceNum: null, priceStr: 'Цена недоступна' };
        }
        const priceStr = data.lowest_price || data.median_price || data.volume || 'Цена недоступна';
        const priceNum = normalizePrice(priceStr);
        return { priceNum, priceStr };
    } catch (error) {
        return { priceNum: null, priceStr: 'Цена недоступна' };
    }
}

async function updateUserItemPrice(chatId, itemName, priceNum) {
    if (!priceNum) return;
    const item = await db.get('SELECT id FROM items WHERE market_hash_name = ?', [itemName]);
    if (!item) return;
    await db.run(
        'UPDATE user_items SET current_price = ?, updated_at = datetime("now") WHERE chat_id = ? AND item_id = ?',
        [priceNum, chatId, item.id]
    );
}

async function ensureInventoryForChat(chatId) {
    const user = await db.get('SELECT steam_id FROM users WHERE chat_id = ?', [chatId]);
    if (!user || !user.steam_id) return { ok: false, error: 'STEAM_ID_MISSING' };
    const profilePrivate = await checkProfilePrivacy(user.steam_id);
    if (profilePrivate) return { ok: false, error: 'PROFILE_PRIVATE' };
    return syncInventory(chatId, user.steam_id);
}

// --- API ---
app.get('/api/categories', async (req, res) => {
    const { chat_id } = req.query;
    if (!chat_id) return res.status(400).json({ error: 'chat_id required' });
    const syncResult = await ensureInventoryForChat(chat_id);
    if (!syncResult.ok) return res.status(400).json({ error: syncResult.error });
    const cats = await db.all(
        'SELECT DISTINCT i.category FROM items i JOIN user_items ui ON i.id = ui.item_id WHERE ui.chat_id = ? ORDER BY i.category',
        [chat_id]
    );
    res.json(cats.map((c) => c.category));
});

app.get('/api/items', async (req, res) => {
    const { chat_id, category, page = 0 } = req.query;
    if (!chat_id || !category) return res.status(400).json({ error: 'chat_id and category required' });
    const offset = Number(page) * 10;
    const items = await db.all(
        'SELECT i.market_hash_name as name, ui.amount FROM items i JOIN user_items ui ON i.id = ui.item_id WHERE ui.chat_id = ? AND i.category = ? ORDER BY i.market_hash_name LIMIT 10 OFFSET ?',
        [chat_id, category, offset]
    );
    res.json(items);
});

app.get('/api/get-price', async (req, res) => {
    const { name, chat_id } = req.query;
    if (!name) return res.status(400).json({ error: 'name required' });
    const priceData = await getSteamPrice(name);
    if (chat_id) {
        await updateUserItemPrice(chat_id, name, priceData.priceNum);
    }
    let tracking = null;
    if (chat_id) {
        tracking = await db.get('SELECT * FROM tracking WHERE chat_id = ? AND item_name = ?', [chat_id, name]);
    }
    res.json({ ...priceData, tracking: Boolean(tracking) });
});

app.post('/api/track', async (req, res) => {
    const { chat_id, name } = req.body;
    if (!chat_id || !name) return res.status(400).json({ error: 'chat_id and name required' });
    const { priceNum, priceStr } = await getSteamPrice(name);
    if (!priceNum) return res.status(400).json({ error: 'PRICE_UNAVAILABLE', message: priceStr });
    await db.run(
        'INSERT INTO tracking (chat_id, item_name, start_price, last_price, started_at) VALUES (?, ?, ?, ?, datetime("now")) ON CONFLICT(chat_id, item_name) DO UPDATE SET last_price = excluded.last_price',
        [chat_id, name, priceNum, priceNum]
    );
    res.json({ success: true });
});

app.post('/api/untrack', async (req, res) => {
    const { chat_id, name } = req.body;
    if (!chat_id || !name) return res.status(400).json({ error: 'chat_id and name required' });
    await db.run('DELETE FROM tracking WHERE chat_id = ? AND item_name = ?', [chat_id, name]);
    res.json({ success: true });
});

// --- BOT ---
bot.start((ctx) => ctx.reply('👋 Привет! Пришли ссылку на свой Steam профиль.'));

bot.on('text', async (ctx) => {
    const message = ctx.message.text.trim();
    if (!message.includes('steamcommunity.com')) {
        return ctx.reply('❗ Пришли ссылку на профиль Steam (например https://steamcommunity.com/id/ваш_ник).');
    }

    try {
        const resolved = await resolveSteamId(message);
        if (resolved.error === 'INVALID_LINK') {
            return ctx.reply('❌ Невалидная ссылка. Пример: https://steamcommunity.com/id/ваш_ник');
        }
        if (resolved.error === 'PROFILE_PRIVATE') {
            return ctx.reply('🔒 Откройте профиль в настройках Steam и попробуйте снова.');
        }

        const steamId = resolved.steamId;
        if (await checkProfilePrivacy(steamId)) {
            return ctx.reply('🔒 Откройте профиль в настройках Steam и попробуйте снова.');
        }

        const inventoryCheck = await fetchInventory(steamId);
        if (!inventoryCheck.ok) {
            if (inventoryCheck.error === 'INVENTORY_PRIVATE') {
                return ctx.reply('🔒 Откройте инвентарь в настройках Steam и попробуйте снова.');
            }
            return ctx.reply('⚠️ Steam сейчас недоступен. Попробуйте чуть позже.');
        }

        await db.run('INSERT OR REPLACE INTO users (chat_id, steam_id) VALUES (?, ?)', [ctx.chat.id, steamId]);

        if (!WEBAPP_URL) {
            return ctx.reply('✅ Steam ID сохранен, но не настроен WEBAPP_URL/DOMAIN.');
        }

        return ctx.reply(
            '✅ Steam ID сохранен. Открывай инвентарь:',
            Markup.keyboard([[Markup.button.webApp('📦 Инвентарь', WEBAPP_URL)]]).resize()
        );
    } catch (error) {
        return ctx.reply('⚠️ Ошибка обработки. Попробуйте позже.');
    }
});

// --- PRICE MONITORING ---
async function checkPrices() {
    const tracks = await db.all('SELECT * FROM tracking');
    for (const t of tracks) {
        const { priceNum, priceStr } = await getSteamPrice(t.item_name);
        if (priceNum && priceNum > t.last_price) {
            bot.telegram.sendMessage(
                t.chat_id,
                `📈 *Цена выросла!*\n${t.item_name}\nБыло: ${t.last_price} -> Стало: ${priceStr}`,
                { parse_mode: 'Markdown' }
            );
            await db.run('UPDATE tracking SET last_price = ? WHERE id = ?', [priceNum, t.id]);
        }
        await new Promise((r) => setTimeout(r, 2500));
    }
}

cron.schedule('0 * * * *', () => {
    checkPrices().catch((error) => console.error('Price check error:', error));
});

initDb().then(() => {
    bot.launch();
    app.listen(PORT, () => console.log(`Server started on ${PORT}`));
});
