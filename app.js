 (cd "$(git rev-parse --show-toplevel)" && git apply --3way <<'EOF' 
diff --git a/app.js b/app.js
index 442266ba7a20664122e4cb2bea9551d95d3abfd8..44470238e2323d0da44615d0cc9e515d8668e2de 100644
--- a/app.js
+++ b/app.js
@@ -69,79 +69,92 @@ function getCategory(name) {
     if (/кейс|case|пакет|набор/.test(n)) return "📦 Кейсы";
     if (/sticker|наклейка/.test(n)) return "🎯 Наклейки";
     if (/agent|агент/.test(n)) return "👤 Агенты";
     if (n.includes('|')) return "🔫 Оружие";
     return "🛠 Прочее";
 }
 
 async function syncInventory(chatId, steamId) {
     try {
         const url = `https://steamcommunity.com/inventory/${steamId}/730/2?l=russian&count=2000`;
         const res = await axios.get(url);
         if (!res.data || !res.data.descriptions) return false;
         const counts = {};
         res.data.descriptions.forEach(d => { if (d.marketable) counts[d.market_hash_name] = (counts[d.market_hash_name] || 0) + 1; });
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
 
+async function ensureInventoryForChat(chatId) {
+    const user = await db.get("SELECT steam_id FROM users WHERE chat_id = ?", [chatId]);
+    if (!user || !user.steam_id) return { ok: false, error: "STEAM_ID_MISSING" };
+    const ok = await syncInventory(chatId, user.steam_id);
+    if (!ok) return { ok: false, error: "INVENTORY_PRIVATE" };
+    return { ok: true };
+}
+
 // --- API ЭНДПОИНТЫ ---
 app.get('/api/categories', async (req, res) => {
-    const cats = await db.all("SELECT DISTINCT i.category FROM items i JOIN user_items ui ON i.id = ui.item_id WHERE ui.chat_id = ?", [req.query.chat_id]);
+    const { chat_id } = req.query;
+    if (!chat_id) return res.status(400).json({ error: 'chat_id required' });
+    const syncResult = await ensureInventoryForChat(chat_id);
+    if (!syncResult.ok) {
+        return res.status(400).json({ error: syncResult.error });
+    }
+    const cats = await db.all("SELECT DISTINCT i.category FROM items i JOIN user_items ui ON i.id = ui.item_id WHERE ui.chat_id = ?", [chat_id]);
     res.json(cats.map(c => c.category));
 });
 
 app.get('/api/items', async (req, res) => {
     const { chat_id, category, page = 0 } = req.query;
     const items = await db.all("SELECT i.name, ui.amount FROM items i JOIN user_items ui ON i.id = ui.item_id WHERE ui.chat_id = ? AND i.category = ? LIMIT 10 OFFSET ?", [chat_id, category, page * 10]);
     res.json(items);
 });
 
 app.get('/api/get-price', async (req, res) => {
     const data = await getSteamPrice(req.query.name);
     res.json(data);
 });
 
 app.post('/api/track', async (req, res) => {
     const { chat_id, name } = req.body;
     const { priceNum } = await getSteamPrice(name);
     if (!priceNum) return res.status(400).json({ error: 'No price' });
     await db.run("INSERT OR REPLACE INTO tracking (chat_id, item_name, last_price) VALUES (?, ?, ?)", [chat_id, name, priceNum]);
     res.json({ success: true });
 });
 
 // --- БОТ ---
 bot.start((ctx) => ctx.reply("👋 Привет! Пришли ссылку на свой Steam профиль."));
 bot.on('text', async (ctx) => {
     if (ctx.message.text.includes('steamcommunity.com')) {
         const sid = await resolveSteamId(ctx.message.text);
-        if (!sid) return ctx.reply("❌ Ошибка ID.");
-        await ctx.reply("⏳ Синхронизирую...");
-        if (await syncInventory(ctx.chat.id, sid)) {
-            await db.run("INSERT OR REPLACE INTO users (chat_id, steam_id) VALUES (?, ?)", [ctx.chat.id, sid]);
-            ctx.reply("✅ Готово!", Markup.keyboard([[Markup.button.webApp("📦 Инвентарь", `https://${process.env.DOMAIN}/index.html`)]]).resize());
-        } else ctx.reply("❌ Профиль закрыт.");
+        if (!sid) return ctx.reply("❌ Не удалось определить Steam ID. Проверь ссылку.");
+        await db.run("INSERT OR REPLACE INTO users (chat_id, steam_id) VALUES (?, ?)", [ctx.chat.id, sid]);
+        ctx.reply("✅ Steam ID сохранен. Открывай инвентарь:", Markup.keyboard([[Markup.button.webApp("📦 Инвентарь", `https://${process.env.DOMAIN}/index.html`)]]).resize());
+        return;
     }
+    ctx.reply("❗ Пришли ссылку на профиль Steam (например https://steamcommunity.com/id/ваш_ник).");
 });
 
 // --- МОНИТОРИНГ ---
 async function checkPrices() {
     const tracks = await db.all("SELECT * FROM tracking");
     for (const t of tracks) {
         const { priceNum, priceStr } = await getSteamPrice(t.item_name);
         if (priceNum && priceNum > t.last_price) {
             bot.telegram.sendMessage(t.chat_id, `📈 *Цена выросла!*\n${t.item_name}\nБыло: ${t.last_price} -> Стало: ${priceStr}`, { parse_mode: 'Markdown' });
             await db.run("UPDATE tracking SET last_price = ? WHERE id = ?", [priceNum, t.id]);
         }
         await new Promise(r => setTimeout(r, 3000));
     }
 }
 setInterval(checkPrices, 3600000);
 
 initDb().then(() => { bot.launch(); app.listen(PORT); });
 
EOF
)
