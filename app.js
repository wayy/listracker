const { Telegraf, Markup } = require('telegraf');
const express = require('express');
const cors = require('cors');
const cron = require('node-cron');
const db = require('./modules/database');
const steam = require('./modules/steam');
require('dotenv').config();

// КОНФИГУРАЦИЯ
const BOT_TOKEN = process.env.BOT_TOKEN;
const WEBAPP_URL = process.env.WEBAPP_URL;
const PORT = process.env.PORT || 3000; // Вернул порт 3000 по просьбе пользователя

const bot = new Telegraf(BOT_TOKEN);
const app = express();

app.use(cors());
app.use(express.json());

// Логирование всех запросов для отладки
app.use((req, res, next) => {
    console.log(`[DEBUG] ${new Date().toISOString()} ${req.method} ${req.url}`);
    console.log(`[DEBUG] Origin: ${req.headers.origin}`);
    next();
});

// Проверка работоспособности
app.get('/', (req, res) => {
    res.send('CS2 Tracker Backend is running! 🚀');
});

// --- ЛОГИКА БОТА ---

bot.start((ctx) => {
    ctx.reply("👋 Привет! Я помогу тебе следить за ценами скинов CS2.\n\nПросто отправь мне ссылку на свой Steam профиль.");
});

bot.on('text', async (ctx) => {
    const text = ctx.message.text;
    if (text.includes('steamcommunity.com')) {
        const msg = await ctx.reply("⏳ Обработка профиля...");
        const steamId = await steam.resolveSteamID(text);

        if (steamId) {
            await db.saveUser(ctx.from.id, steamId, ctx.from.first_name);
            console.log(`[BOT] User registered: ${ctx.from.id} -> ${steamId}`);

            try { await ctx.telegram.deleteMessage(ctx.chat.id, msg.message_id); } catch (e) { }

            await ctx.reply(
                "✅ Ссылка принята! Теперь ты можешь открыть свой инвентарь.",
                Markup.keyboard([
                    Markup.button.webApp("📦 Инвентарь CS2", `${WEBAPP_URL}?tg_id=${ctx.from.id}`)
                ]).resize()
            );
        } else {
            await ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null, "❌ Не удалось найти Steam ID. Убедитесь, что ссылка верна.");
        }
    }
});

// --- API ДЛЯ MINI APP ---

app.get('/api/inventory', async (req, res) => {
    const tgId = req.query.tg_id;
    console.log(`[API] Inventory request for user: ${tgId}`);

    if (!tgId) return res.status(400).json({ error: "Missing tg_id" });

    try {
        const user = await db.getUser(Number(tgId));
        if (!user) {
            console.warn(`[API] User ${tgId} not found in DB`);
            return res.status(404).json({ error: "User not found. Зарегистрируйтесь в боте заново." });
        }

        let items = [];
        let isCached = false;

        try {
            items = await steam.getInventory(user.steam_id);
            if (items && items.length > 0) {
                await db.updateUserInventory(tgId, items);
                // Проверка на пропавшие отслеживаемые предметы
                const currentNames = items.map(i => i.market_hash_name);
                await db.checkTrackedItemsAvailability(tgId, currentNames);
            }
        } catch (steamErr) {
            console.warn(`[API] Steam Error for ${tgId}: ${steamErr.message}. Checking cache...`);
            // Тут была ошибка "is not a function" - теперь мы уверены что она есть
            if (typeof db.getCachedInventory === 'function') {
                items = await db.getCachedInventory(tgId);
                isCached = true;
            } else {
                console.error("[CRITICAL] db.getCachedInventory is still missing in memory!");
                throw steamErr;
            }

            if (!items || items.length === 0) throw steamErr;
        }

        res.json({ items, cached: isCached });
    } catch (e) {
        console.error("[API] Fatal Error:", e.message);
        res.status(500).json({ error: e.message });
    }
});

// Прочие эндпоинты
app.get('/api/price', async (req, res) => {
    try {
        const data = await steam.getPrice(req.query.name);
        res.json(data || { error: "Not found" });
    } catch (e) { res.status(500).json({ error: e.message }); }
});

app.post('/api/track', async (req, res) => {
    const { tg_id, name, price, currency } = req.body;
    try {
        const result = await db.addTracking(tg_id, name, price, currency);
        res.json(result);
    } catch (e) { res.status(500).json({ error: e.message }); }
});

app.post('/api/untrack', async (req, res) => {
    try {
        await db.removeTracking(req.body.tg_id, req.body.name);
        res.json({ status: 'success' });
    } catch (e) { res.status(500).json({ error: e.message }); }
});

app.get('/api/tracked', async (req, res) => {
    try {
        const tracked = await db.getTrackedItemsForUser(req.query.tg_id);
        res.json({ tracked });
    } catch (e) { res.status(500).json({ error: e.message }); }
});

// Cron job
cron.schedule('0 * * * *', async () => {
    console.log("[CRON] Checking prices...");
    const tracks = await db.getAllTrackingItems();
    for (const track of tracks) {
        const data = await steam.getPrice(track.market_hash_name);
        if (data && data.price > track.last_price) {
            const msg = `📈 *Цена выросла!*\n\n${track.market_hash_name}\nБыло: ${track.last_price} -> Стало: ${data.text}`;
            try {
                await bot.telegram.sendMessage(track.telegram_user_id, msg, { parse_mode: 'Markdown' });
                await db.updateLastPrice(track.id, data.price);
            } catch (e) { }
        }
        await new Promise(r => setTimeout(r, 2000));
    }
});

bot.launch();
app.listen(PORT, '0.0.0.0', () => console.log(`[SERVER] Started on port ${PORT}`));

process.once('SIGINT', () => { bot.stop('SIGINT'); process.exit(); });
process.once('SIGTERM', () => { bot.stop('SIGTERM'); process.exit(); });
