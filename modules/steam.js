const axios = require('axios');
const cheerio = require('cheerio');

const STEAM_INVENTORY_URL = (steamId) => `https://steamcommunity.com/inventory/${steamId}/730/2?l=russian&count=5000`;
const STEAM_PRICE_URL = (hashName) => `https://steamcommunity.com/market/priceoverview/?appid=730&currency=5&market_hash_name=${encodeURIComponent(hashName)}`;

module.exports = {
    // Получение SteamID64 из ссылки
    resolveSteamID: async (url) => {
        // Очистка URL
        url = url.replace(/\/$/, '');

        if (url.includes('/profiles/')) {
            const match = url.match(/profiles\/(\d+)/);
            return match ? match[1] : null;
        } else if (url.includes('/id/')) {
            try {
                const response = await axios.get(url);
                const $ = cheerio.load(response.data);
                // Steam хранит ID в переменной g_rgProfileData в скриптах
                const html = $.html();
                const match = html.match(/"steamid":"(\d+)"/);
                return match ? match[1] : null;
            } catch (e) {
                console.error("Error parsing profile:", e.message);
                return null;
            }
        }
        return null;
    },

    // Получение инвентаря
    getInventory: async (steamId) => {
        try {
            const url = STEAM_INVENTORY_URL(steamId);
            console.log(`Fetching inventory from: ${url}`);
            const response = await axios.get(url);
            const data = response.data;

            if (!data || !data.assets || !data.descriptions) {
                console.warn("Steam response missing assets or descriptions");
                return [];
            }

            // Создаем карту описаний для быстрого поиска
            const descriptionsMap = {};
            data.descriptions.forEach(d => {
                descriptionsMap[`${d.classid}_${d.instanceid}`] = d;
            });

            // Сопоставление assets и descriptions
            const items = data.assets.map(asset => {
                const desc = descriptionsMap[`${asset.classid}_${asset.instanceid}`];
                if (!desc) return null;

                // CS2 предметы обычно marketable, но некоторые (медали и т.д.) нет.
                // Нам нужны скины. У скинов есть market_hash_name.
                // Убираем жесткую проверку marketale, так как иногда Steam чудит с флагами
                if (!desc.market_hash_name) return null;

                // Фильтруем базовые предметы, которые нельзя продать/передать, если у них нет цены?
                // Обычно marketable=1 это гарантия. Но если пусто - попробуем вернуть все что похоже на скин.
                // Лучше полагаться на marketable, но если пользователь говорит "пусто", может флаг подвел.
                // Давайте вернем marketable || (tradable && type != "Base Grade Container")
                // Для простоты пока вернем всё что имеет хеш-нейм и иконку.

                return {
                    name: desc.market_name,
                    market_hash_name: desc.market_hash_name,
                    image: `https://community.cloudflare.steamstatic.com/economy/image/${desc.icon_url}`,
                    type: desc.type
                };
            }).filter(item => item !== null);

            console.log(`Parsed ${items.length} items from inventory`);
            return items;
        } catch (e) {
            console.error("Steam Inventory Error:", e.message);
            if (e.response && e.response.status === 429) {
                throw new Error("Steam Rate Limit (429). Попробуйте позже.");
            }
            if (e.response && e.response.status === 403) {
                throw new Error("Steam Inventory Private (403). Проверьте настройки приватности.");
            }
            throw new Error("Failed to fetch inventory. Profile might be private or Steam is down.");
        }
    },

    // Получение цены
    getPrice: async (marketHashName) => {
        try {
            const response = await axios.get(STEAM_PRICE_URL(marketHashName));
            if (response.data && response.data.lowest_price) {
                // Цена приходит в формате "123,45 pуб."
                let priceStr = response.data.lowest_price;
                // Убираем символы валюты и заменяем запятую на точку
                let priceVal = parseFloat(priceStr.replace(/[^\d.,]/g, '').replace(',', '.'));
                return {
                    price: priceVal,
                    text: priceStr
                };
            }
            return null;
        } catch (e) {
            console.error(`Price fetch error for ${marketHashName}:`, e.message);
            return null;
        }
    }
};
