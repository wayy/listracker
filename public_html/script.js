 (cd "$(git rev-parse --show-toplevel)" && git apply --3way <<'EOF' 
diff --git a/public_html/script.js b/public_html/script.js
index 143e70fec458a41338e616ce2bcdc375762d89d4..72e60565eabe294a7a15e5d002ef35b9f1480901 100644
--- a/public_html/script.js
+++ b/public_html/script.js
@@ -1,48 +1,74 @@
 const tg = window.Telegram.WebApp;
 const chatId = tg.initDataUnsafe?.user?.id;
 let currentCat = '', page = 0, selected = '';
+const categoriesEl = document.getElementById('categories');
+const itemsListEl = document.getElementById('items-list');
+const pageNumEl = document.getElementById('page-num');
 
 async function loadCats() {
+    if (!chatId) {
+        tg.showAlert('Не удалось определить Telegram ID.');
+        return;
+    }
+    categoriesEl.innerHTML = '<div class="loading">⏳ Загружаю категории...</div>';
     const res = await fetch(`/api/categories?chat_id=${chatId}`);
     const cats = await res.json();
-    document.getElementById('categories').innerHTML = cats.map(c => `<button onclick="setCat(this, '${c}')">${c}</button>`).join('');
+    if (!res.ok) {
+        const message = cats?.error === 'INVENTORY_PRIVATE'
+            ? 'Инвентарь закрыт. Открой профиль и попробуй снова.'
+            : 'Не удалось загрузить инвентарь. Напиши боту ссылку на Steam профиль.';
+        categoriesEl.innerHTML = '';
+        tg.showAlert(message);
+        return;
+    }
+    categoriesEl.innerHTML = cats.map(c => `<button onclick="setCat(this, '${c}')">${c}</button>`).join('');
+    const firstBtn = categoriesEl.querySelector('button');
+    if (firstBtn) {
+        setCat(firstBtn, firstBtn.textContent);
+    }
 }
 
 async function setCat(btn, cat) {
     document.querySelectorAll('.tabs button').forEach(b => b.classList.remove('active'));
     btn.classList.add('active');
     currentCat = cat; page = 0; loadItems();
 }
 
 async function loadItems() {
+    itemsListEl.innerHTML = '<div class="loading">⏳ Загружаю предметы...</div>';
     const res = await fetch(`/api/items?chat_id=${chatId}&category=${encodeURIComponent(currentCat)}&page=${page}`);
     const items = await res.json();
-    document.getElementById('page-num').innerText = `Стр. ${page + 1}`;
-    document.getElementById('items-list').innerHTML = items.map(i => `
+    pageNumEl.innerText = `Стр. ${page + 1}`;
+    if (!items.length) {
+        itemsListEl.innerHTML = '<div class="loading">Нет предметов в этой категории.</div>';
+        return;
+    }
+    itemsListEl.innerHTML = items.map(i => `
         <div class="card">
             <div>${i.name}</div>
+            <div class="amount">x${i.amount}</div>
             <button onclick="showPrice('${i.name}')">Цена</button>
         </div>
     `).join('');
 }
 
 async function showPrice(name) {
     selected = name;
     document.getElementById('modal').style.display = 'flex';
     document.getElementById('m-name').innerText = name;
     document.getElementById('m-price').innerText = '⏳ Загрузка...';
     const res = await fetch(`/api/get-price?name=${encodeURIComponent(name)}`);
     const data = await res.json();
     document.getElementById('m-price').innerText = data.priceStr;
 }
 
 async function trackItem() {
     const res = await fetch('/api/track', {
         method: 'POST',
         headers: {'Content-Type': 'application/json'},
         body: JSON.stringify({ chat_id: chatId, name: selected })
     });
     if(res.ok) { tg.showAlert('Отслеживание включено!'); closeModal(); }
 }
 
 function nextPage() { page++; loadItems(); }
 
EOF
)
