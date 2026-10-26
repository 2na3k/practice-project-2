# Remymy

Remymy is a small food discovery app. A person describes a craving in the chat, and connected chat answers through the Pi agent, which calls the Python MCP service for real Food.com recipes. Recipes is a browsable grid of the imported recipe records, and Saved and History keep what the person liked.

Chat needs an AI provider. Connect one from the AI connection panel (`/api/connection`) before sending a message; until then the composer opens that panel. The connected model reads each request through the Pi catalog (`@earendil-works/pi-ai` and `@earendil-works/pi-agent-core`), and the rule-based reader in `lib/server/chat-service.ts` keeps the diet, time, and exclusion preferences from one message to the next. This is a focused Pi Agent integration, not the full coding-agent CLI.

The app includes a small MCP integration. The Pi agent exposes MCP tools as native agent tools, so connected chat requests can call the Python MCP service through Streamable HTTP. The server does not persist conversations, preferences, saved meals, or API keys. A connected provider key is held in an in-memory session behind an HTTP-only cookie for eight hours. The session store is process-local, so a multi-instance deployment needs a shared session design before it can support reconnects across instances.

## Run locally

From this directory:

```bash
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). If port 3000 is occupied, use `npm run dev -- --port 3100` and open [http://localhost:3100](http://localhost:3100). The app binds to `0.0.0.0`, so it can also be reached from another device on the same network when the host firewall allows it.

Available checks and production commands:

```bash
npm run test       # Run the Vitest behavior and server-boundary suites
npm run typecheck  # Check TypeScript without emitting files
npm run lint       # Run ESLint
npm run format     # Format source files with Prettier
npm run build      # Create a production Next.js build
npm run start      # Serve the production build
```

Run `npm run build` before `npm run start`; `start` expects a build in `.next`.

## Run the app container

The standalone Docker image builds and serves the Next.js app without needing the MCP, model, or database containers:

```bash
docker build -t remymy-app:dev .
docker run --rm -p 3000:3000 remymy-app:dev
```

From `SystemCode/remymy`, the shared Compose file runs the app and its Python MCP service together:

```bash
docker compose up --build
```

Open [http://localhost:3000](http://localhost:3000). The app container calls the MCP container at `http://mcp:8000/mcp`; the MCP service is also available from the host at [http://localhost:8000/health](http://localhost:8000/health).

## Product behavior

- Discover opens with a short welcome above the composer. Once the person sends a request, it becomes a conversation; a meal the agent suggests opens its details.
- Each person gets one guest user per browser. `/api/guest` asks the Data API to create it on the first visit and keeps its username in an HTTP-only cookie (`remymy-guest`), so history, likes, ratings, saved recipes, preferences and the recipes the person writes follow the browser. Clearing cookies starts a new guest.
- Above the welcome, "Picked for you" shows the Data API's model recommendations for the guest ("Recipes you'll probably like", with their predicted chance of being liked) through the app's `/api/recommend/[username]` route; set `DATA_API_URL` when the API is not at `http://localhost:8002`. The "Less overthinking. More good food." banner shows under it only when the API returns no picks.
- Recipes browses the Data API's recipe records with search and paging. Opening one shows its details from `/api/recipes/[id]`.
- Each recipe in the priced catalog has a tick (liked it), a cross (not for me), and a bookmark. A like goes through `/api/recipes/[id]/rating` to the Data API's `PUT /api/recipes/{recipe_id}/rating`, under this browser's guest user. A bookmark saves the recipe in the Data API (`/api/users/[username]/saved`), so Saved follows the guest; recipes this browser saved before that (`remymy-saved-foods`) go up to it once.
- Clicking a recipe in "Picked for you", Saved or History opens its details: ingredients with their FairPrice pack prices, the basket price, nutrients per 100 g, its tags and similar recipes, from `/api/catalog/[id]` (the Data API's `GET /api/recipe/{fdc_id}`).
- Preferences sets the diet, budget, allergies, cuisine and preferred nutrient the recommendations use; they are saved for the guest in the Data API and kept in this browser.
- Add recipe writes a recipe of the person's own, with tags the classifier suggests; My recipes lists the guest's recipes, newest first, and opens each one.
- History lists what the guest rated, newest first, from `/api/users/[username]/history`. A recipe's title opens it.
- Likes shown in Saved come back from `/api/users/[username]/likes`, so the buttons show what the database holds.
- Chat keeps the preferences from one message to the next. The server reads each turn with the rule-based parser in `lib/preferences.ts`; hard constraints such as a vegan diet or a time limit carry forward until the person relaxes them.
- Provider and model choices come from the Pi model catalog exposed by the server, so the UI keeps no second model list. Connected mode validates the provider and model before opening an eight-hour session.
- Chat history is discarded when the page or server session ends.

## Project layout

```text
app/             Next.js routes (chat, connection, recipes, catalog, users, guest) and global styles
components/      Composer, sidebar, recipe and catalog details, connection panel, meal detail
lib/preferences.ts  Rule-based reader of a chat turn into dietary, time, and exclusion preferences
lib/server/      Pi catalog, chat service, MCP client, request guards, and in-memory connection sessions
```

The repository also contains the Python MCP service under `SystemCode/remymy/mcp`. It exposes a small `say_hello` tool over Streamable HTTP. The app calls it through its same-origin `/api/mcp` route and the Pi chat agent can call it directly. Set `MCP_URL` to the MCP endpoint when running the app outside Docker; it defaults to `http://localhost:8000/mcp`.

## Data and privacy boundary

No account, analytics, database, or MCP service is required to run the app. Preference changes and saved meals remain in this browser's local storage. Connected API keys are verified with the provider, then saved on the server in an encrypted file (`SESSION_STORE_PATH`, default `.remymy/sessions.json`), encrypted with AES-256-GCM using a key derived from `SESSION_SECRET`. A saved connection survives a restart of the app until its eight-hour session expires or the secret changes; the browser keeps only an HTTP-only session cookie. Keys are never included in the public provider/model catalog or connection status. Set `SESSION_SECRET` to a private value for any shared environment. Do not enter sensitive health information.

The automated suite uses mocked provider and agent behavior and does not make real model calls. A live connected response has not been verified here because no provider key is available; treat a successful local test run as a contract check rather than proof of provider credentials, network access, or deployment behavior.

## Verification

See [TESTING.md](./TESTING.md) for the automated and hands-on browser checks.
