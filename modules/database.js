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
