const { Telegraf, Markup } = require('telegraf');
const express = require('express');
const cors = require('cors');
const cron = require('node-cron');
const db = require('./modules/database');
const steam = require('./modules/steam');
require('dotenv').config();

// КОНФИГУРАЦИЯ
const BOT_TOKEN = process.env.BOT_TOKEN;
const PORT = process.env.PORT || 3000;
const WEBAPP_URL = process.env.WEBAPP_URL;

const bot = new Telegraf(BOT_TOKEN);
const app = express();

app.use(cors()); // Разрешаем запросы с GitHub Pages
app.use(express.json());

// --- ЛОГИКА БОТА ---

bot.start((ctx) => {
    ctx.reply(
        "Привет! Я помогу отслеживать цены на твои скины в CS2.\n\n" +
        "Отправь мне ссылку на твой Steam-профиль.\n" +
        "Пример: https://steamcommunity.com/id/gabene или https://steamcommunity.com/profiles/76561198000000000"
    );
});

bot.on('text', async (ctx) => {
    const text = ctx.message.text.trim();

    // Простая валидация ссылки
    if (text.includes('steamcommunity.com')) {
        try {
            const msg = await ctx.reply("Проверяю профиль...");
            const steamId = await steam.resolveSteamID(text);

            if (!steamId) {
                return ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null, "Не удалось найти Steam ID. Убедитесь, что ссылка верна и профиль открыт.");
            }

            // Сохраняем пользователя
            await db.saveUser(ctx.from.id, steamId, ctx.from.first_name);

            // Удаляем сообщение "Проверяю...", чтобы не засорять чат (опционально)
            try {
                await ctx.telegram.deleteMessage(ctx.chat.id, msg.message_id);
            } catch (e) { }

            await ctx.reply(
                "Профиль привязан! Теперь ты можешь открыть инвентарь.",
                Markup.keyboard([
                    Markup.button.webApp("📦 Инвентарь CS2", WEBAPP_URL)
                ]).resize()
            );

        } catch (e) {
            console.error(e);
            ctx.reply("Произошла ошибка при обработке ссылки.");
        }
    } else {
        ctx.reply("Пожалуйста, отправь корректную ссылку на Steam-профиль.");
    }
});

// --- API ДЛЯ MINI APP ---

// 1. Получение инвентаря (Mini App запрашивает у нас, мы у Steam)
app.get('/api/inventory', async (req, res) => {
    const tgId = req.query.tg_id;

    if (!tgId) return res.status(400).json({ error: "Missing tg_id" });

    try {
        const user = await db.getUser(tgId);
        if (!user) return res.status(404).json({ error: "User not found" });

        const items = await steam.getInventory(user.steam_id);

        // Синхронизация с БД
        if (items.length > 0) {
            // Сохраняем инвентарь (кеш)
            await db.updateUserInventory(tgId, items);

            // Проверка на пропавшие предметы
            const currentItemNames = items.map(i => i.market_hash_name);
            const removedItems = await db.checkTrackedItemsAvailability(tgId, currentItemNames);

            if (removedItems.length > 0) {
                console.log(`Stopped tracking for items: ${removedItems.join(', ')}`);
                // Опционально: можно уведомить пользователя, что предмет пропал и отслеживание остановлено
            }
        }

        res.json({ items });
    } catch (e) {
        console.error("Inventory Error:", e);
        res.status(500).json({ error: e.message });
    }
});

// 2. Получение цены предмета
app.get('/api/price', async (req, res) => {
    const name = req.query.name;
    try {
        const priceData = await steam.getPrice(name);
        if (!priceData) return res.status(404).json({ error: "Price not found" });
        res.json(priceData);
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// 3. Добавление в отслеживание
app.post('/api/track', async (req, res) => {
    const { tg_id, name, price, currency } = req.body;
    try {
        const result = await db.addTracking(tg_id, name, price, currency);
        res.json(result);
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// 4. Удаление из отслеживания
app.post('/api/untrack', async (req, res) => {
    const { tg_id, name } = req.body;
    try {
        await db.removeTracking(tg_id, name);
        res.json({ status: 'success' });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// 5. Получение списка отслеживаемых
app.get('/api/tracked', async (req, res) => {
    const tgId = req.query.tg_id;
    if (!tgId) return res.status(400).json({ error: "Missing tg_id" });

    try {
        const tracked = await db.getTrackedItemsForUser(tgId);
        res.json({ tracked });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// --- CRON JOB (Проверка цен каждый час) ---
// Задача запускается в 0 минут каждого часа
cron.schedule('0 * * * *', async () => {
    console.log("Running price check...");
    try {
        const tracks = await db.getAllTrackingItems();

        for (const track of tracks) {
            // Эмуляция задержки для избежания rate limit
            await new Promise(r => setTimeout(r, 2000));

            try {
                const currentData = await steam.getPrice(track.market_hash_name);

                if (currentData) {
                    // Если цена выросла
                    if (currentData.price > track.last_price) {
                        const diff = (currentData.price - track.last_price).toFixed(2);
                        const msg = `📈 Цена на <b>${track.market_hash_name}</b> выросла!\n` +
                            `Было: ${track.last_price} руб.\n` +
                            `Стало: ${currentData.text} (+${diff})`;

                        try {
                            await bot.telegram.sendMessage(track.telegram_user_id, msg, { parse_mode: 'HTML' });
                        } catch (err) {
                            console.error(`Failed to send message to ${track.telegram_user_id}:`, err.message);
                        }
                    }

                    // Обновляем последнюю известную цену, если она отличается
                    if (currentData.price !== track.last_price) {
                        db.updateLastPrice(track.id, currentData.price);
                    }
                }
            } catch (innerErr) {
                console.error(`Error checking item ${track.market_hash_name}:`, innerErr);
            }
        }
    } catch (e) {
        console.error("Cron Error:", e);
    }
});

// Запуск
bot.launch().then(() => {
    console.log('Telegram bot started');
}).catch(err => {
    console.error("Bot launch error:", err);
});

app.listen(PORT, () => {
    console.log(`Backend API running on port ${PORT}`);
});

// Graceful stop
process.once('SIGINT', () => bot.stop('SIGINT'));
process.once('SIGTERM', () => bot.stop('SIGTERM'));
