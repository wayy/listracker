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
            const response = await axios.get(STEAM_INVENTORY_URL(steamId));
            const data = response.data;
            
            if (!data || !data.assets || !data.descriptions) return [];

            // Сопоставление assets и descriptions
            const items = data.assets.map(asset => {
                const desc = data.descriptions.find(d => d.classid === asset.classid && d.instanceid === asset.instanceid);
                if (!desc || !desc.marketable) return null; // Игнорируем непродаваемые

                return {
                    name: desc.market_name,
                    market_hash_name: desc.market_hash_name,
                    image: `https://community.cloudflare.steamstatic.com/economy/image/${desc.icon_url}`,
                    type: desc.type
                };
            }).filter(item => item !== null);

            return items;
        } catch (e) {
            console.error("Steam Inventory Error:", e.message);
            throw new Error("Failed to fetch inventory. Profile might be private.");
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
