const axios = require('axios');

const HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
};

const STEAM_INVENTORY_URL = (steamId) => `https://steamcommunity.com/inventory/${steamId}/730/2?l=russian&count=2000`;
const STEAM_PRICE_URL = (hashName) => `https://steamcommunity.com/market/priceoverview/?appid=730&currency=5&market_hash_name=${encodeURIComponent(hashName)}`;

module.exports = {
    // Получение SteamID64 из ссылки
    resolveSteamID: async (url) => {
        url = url.replace(/\/$/, '');
        if (url.includes('/profiles/')) {
            const match = url.match(/profiles\/(\d+)/);
            return match ? match[1] : null;
        } else if (url.includes('/id/')) {
            try {
                // Пытаемся получить XML версию для точного ID как в примере
                const response = await axios.get(`${url}/?xml=1`, { headers: HEADERS });
                const match = response.data.match(/<steamID64>(\d+)<\/steamID64>/);
                if (match) return match[1];

                // Fallback на обычный HTML
                const htmlResponse = await axios.get(url, { headers: HEADERS });
                const htmlMatch = htmlResponse.data.match(/"steamid":"(\d+)"/);
                return htmlMatch ? htmlMatch[1] : null;
            } catch (e) {
                console.error("Error resolving SteamID:", e.message);
                return null;
            }
        }
        return null;
    },

    // Получение инвентаря
    getInventory: async (steamId) => {
        try {
            const url = STEAM_INVENTORY_URL(steamId);
            console.log(`[STEAM] Fetching inventory: ${url}`);

            const response = await axios.get(url, { headers: HEADERS, timeout: 20000 });
            const data = response.data;

            if (!data || !data.assets || !data.descriptions) {
                console.warn("[STEAM] Empty response or private profile");
                return [];
            }

            const descriptionsMap = {};
            data.descriptions.forEach(d => {
                descriptionsMap[`${d.classid}_${d.instanceid}`] = d;
            });

            const items = data.assets.map(asset => {
                const desc = descriptionsMap[`${asset.classid}_${asset.instanceid}`];
                if (!desc || !desc.market_hash_name) return null;

                return {
                    name: desc.market_name,
                    market_hash_name: desc.market_hash_name,
                    image: `https://community.cloudflare.steamstatic.com/economy/image/${desc.icon_url}`,
                    type: desc.type
                };
            }).filter(item => item !== null);

            console.log(`[STEAM] Found ${items.length} items`);
            return items;
        } catch (e) {
            const status = e.response ? e.response.status : 'Network Error';
            console.error(`[STEAM] Inventory Error (${status}):`, e.message);

            if (status === 429) throw new Error("Steam Rate Limit (429). Попробуйте через 5-10 минут.");
            if (status === 403) throw new Error("Steam Profile Private (403). Откройте инвентарь в настройках.");
            throw new Error(`Steam Error: ${e.message}`);
        }
    },

    // Получение цены
    getPrice: async (marketHashName) => {
        try {
            const response = await axios.get(STEAM_PRICE_URL(marketHashName), { headers: HEADERS });
            if (response.data && response.data.lowest_price) {
                let priceStr = response.data.lowest_price;
                let priceVal = parseFloat(priceStr.replace(/[^\d.,]/g, '').replace(',', '.'));
                return { price: priceVal, text: priceStr };
            }
            return null;
        } catch (e) {
            console.error("[STEAM] Price Error:", e.message);
            return null;
        }
    }
};
