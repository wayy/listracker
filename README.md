# CS2 Price Tracker Bot + Mini App

Telegram Bot integrated with a Mini App to track prices of Counter-Strike 2 items.

## Features
- **Telegram Bot**: Links your Steam profile.
- **Mini App**: Browses your CS2 inventory, displays current prices.
- **Price Tracking**: Track items and get notified when price increases.
- **Inventory Sync**: Automatically updates inventory changes and stops tracking sold items.

## Setup

### Prerequisites
- [Node.js](https://nodejs.org/) (v16 or higher)
- Telegram Bot Token (from @BotFather)

### Installation

1.  Clone the repository:
    ```bash
    git clone https://github.com/wayy/listracker.git
    cd listracker
    ```

2.  Install dependencies:
    ```bash
    npm install
    ```

3.  Configure:
    - Open `app.js`.
    - Set `BOT_TOKEN`.
    - Set `WEBAPP_URL` to your GitHub Pages URL (e.g. `https://yourusername.github.io/listracker/`).

4.  Run Backend:
    ```bash
    node app.js
    ```

5.  Deploy Frontend:
    - Push `public_html` to GitHub Pages or host it on any static site hosting.
    - Ensure `public_html/script.js` has the correct `API_BASE_URL` pointing to your backend (you might need a tunnel like ngrok if running backend locally).

## API Endpoints
- `GET /api/inventory?tg_id=...` - Get user inventory
- `GET /api/price?name=...` - Get item price
- `POST /api/track` - Start tracking item
- `POST /api/untrack` - Stop tracking item
- `GET /api/tracked?tg_id=...` - Get list of tracked items

## Notes
- Database is SQLite (`tracker.db`), created automatically.
- Prices are checked every hour via Cron job.
