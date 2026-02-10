const sqlite3 = require('sqlite3').verbose();
const path = require('path');

const dbPath = path.resolve(__dirname, '../tracker.db');
const db = new sqlite3.Database(dbPath);

// Инициализация таблиц
db.serialize(() => {
    db.run(`CREATE TABLE IF NOT EXISTS users (
        telegram_user_id INTEGER PRIMARY KEY,
        steam_id TEXT,
        username TEXT
    )`);

    db.run(`CREATE TABLE IF NOT EXISTS tracking (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_user_id INTEGER,
        market_hash_name TEXT,
        start_price REAL,
        last_price REAL,
        currency TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )`);

    db.run(`CREATE TABLE IF NOT EXISTS user_items (
        telegram_user_id INTEGER,
        market_hash_name TEXT,
        category TEXT,
        current_price REAL,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (telegram_user_id, market_hash_name) 
    )`);
});

module.exports = {
    getUser: (tgId) => {
        return new Promise((resolve, reject) => {
            db.get("SELECT * FROM users WHERE telegram_user_id = ?", [tgId], (err, row) => {
                if (err) reject(err);
                else resolve(row);
            });
        });
    },
    saveUser: (tgId, steamId, username) => {
        return new Promise((resolve, reject) => {
            db.run(`INSERT OR REPLACE INTO users (telegram_user_id, steam_id, username) VALUES (?, ?, ?)`, 
                [tgId, steamId, username], (err) => {
                if (err) reject(err);
                else resolve();
            });
        });
    },
    // Обновление кеша инвентаря пользователя
    updateUserInventory: (tgId, items) => {
        return new Promise((resolve, reject) => {
            db.serialize(() => {
                db.run("BEGIN TRANSACTION");
                
                // Удаляем старые записи
                db.run("DELETE FROM user_items WHERE telegram_user_id = ?", [tgId]);
                
                // Вставляем новые
                const stmt = db.prepare("INSERT INTO user_items (telegram_user_id, market_hash_name, category) VALUES (?, ?, ?)");
                for (const item of items) {
                    // Простая логика категории для БД: берем часть до " | "
                    let category = item.name.split(' | ')[0]; 
                    if (category.includes('Sticker')) category = 'Stickers';
                    if (category.includes('Case')) category = 'Cases';
                    if (category.includes('Graffiti')) category = 'Graffiti';

                    stmt.run(tgId, item.market_hash_name, category);
                }
                stmt.finalize();
                
                db.run("COMMIT", (err) => {
                    if (err) reject(err);
                    else resolve();
                });
            });
        });
    },
    // Проверка наличия отслеживаемых предметов в новом инвентаре
    checkTrackedItemsAvailability: (tgId, currentItemNames) => {
        return new Promise((resolve, reject) => {
            // Получаем все отслеживаемые предметы пользователя
            db.all("SELECT market_hash_name FROM tracking WHERE telegram_user_id = ?", [tgId], (err, rows) => {
                if (err) {
                    return reject(err);
                }
                
                const trackedNames = new Set(rows.map(r => r.market_hash_name));
                const currentNames = new Set(currentItemNames);
                const toRemove = [];

                for (const name of trackedNames) {
                    if (!currentNames.has(name)) {
                        toRemove.push(name);
                    }
                }

                if (toRemove.length > 0) {
                    // Удаляем отсутствующие предметы из отслеживания
                    // Важно: node-sqlite3 не поддерживает массивы в IN коастомно, нужно формировать строку
                    const placeholders = toRemove.map(() => '?').join(',');
                    const sql = `DELETE FROM tracking WHERE telegram_user_id = ? AND market_hash_name IN (${placeholders})`;
                    const params = [tgId, ...toRemove];

                    db.run(sql, params, (err) => {
                            if (err) reject(err);
                            else resolve(toRemove); // Возвращаем список удаленных
                        }
                    );
                } else {
                    resolve([]);
                }
            });
        });
    },
    addTracking: (tgId, hashName, price, currency) => {
        return new Promise((resolve, reject) => {
            // Проверяем, не отслеживается ли уже этот предмет этим пользователем
            db.get("SELECT id FROM tracking WHERE telegram_user_id = ? AND market_hash_name = ?", [tgId, hashName], (err, row) => {
                if (row) {
                    resolve({ status: 'already_tracked' });
                } else {
                    db.run(`INSERT INTO tracking (telegram_user_id, market_hash_name, start_price, last_price, currency) VALUES (?, ?, ?, ?, ?)`,
                        [tgId, hashName, price, price, currency], function(err) {
                        if (err) reject(err);
                        else resolve({ status: 'success', id: this.lastID });
                    });
                }
            });
        });
    },
    removeTracking: (tgId, hashName) => {
        return new Promise((resolve, reject) => {
            db.run("DELETE FROM tracking WHERE telegram_user_id = ? AND market_hash_name = ?", [tgId, hashName], (err) => {
                if (err) reject(err);
                else resolve();
            });
        });
    },
    getTrackedItemsForUser: (tgId) => {
        return new Promise((resolve, reject) => {
             db.all("SELECT market_hash_name FROM tracking WHERE telegram_user_id = ?", [tgId], (err, rows) => {
                if (err) reject(err);
                else resolve(rows.map(r => r.market_hash_name));
            });
        });
    },
    getAllTrackingItems: () => {
        return new Promise((resolve, reject) => {
            db.all("SELECT * FROM tracking", [], (err, rows) => {
                if (err) reject(err);
                else resolve(rows);
            });
        });
    },
    updateLastPrice: (id, price) => {
        db.run("UPDATE tracking SET last_price = ? WHERE id = ?", [price, id]);
    }
};
