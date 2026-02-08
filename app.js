const { Telegraf, Markup } = require('telegraf');
const express = require('express');
const cors = require('cors');
const cron = require('node-cron');
const db = require('./modules/database');
const steam = require('./modules/steam');

// КОНФИГУРАЦИЯ
const BOT_TOKEN = '5070946103:AAFG8N40n9IPR3APhYxMeD-mB81-D7ss7Es'; // Вставьте токен
const PORT = 3000;
const WEBAPP_URL = 'https://wayy.github.io/listracker/'; // URL вашего Mini App

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
            ctx.reply("Проверяю профиль...");
            const steamId = await steam.resolveSteamID(text);

            if (!steamId) {
                return ctx.reply("Не удалось найти Steam ID. Убедитесь, что ссылка верна и профиль открыт.");
            }

            // Сохраняем пользователя
            await db.saveUser(ctx.from.id, steamId, ctx.from.first_name);

            ctx.reply(
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
        res.json({ items });
    } catch (e) {
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

// --- CRON JOB (Проверка цен каждый час) ---

cron.schedule('0 * * * *', async () => {
    console.log("Running price check...");
    try {
        const tracks = await db.getAllTrackingItems();
        
        for (const track of tracks) {
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
                        console.error(`Failed to send message to ${track.telegram_user_id}`);
                    }
                }
                
                // Обновляем последнюю известную цену
                db.updateLastPrice(track.id, currentData.price);
            }
            // Небольшая задержка, чтобы не спамить Steam (хотя API у нас "ручной")
            await new Promise(r => setTimeout(r, 2000));
        }
    } catch (e) {
        console.error("Cron Error:", e);
    }
});

// Запуск
bot.launch();
app.listen(PORT, () => {
    console.log(`Backend API running on port ${PORT}`);
});

// Graceful stop
process.once('SIGINT', () => bot.stop('SIGINT'));
process.once('SIGTERM', () => bot.stop('SIGTERM'));
