# Remymy

Remymy is a small meal discovery app with a conversational Discover view. A user can describe a craving, adjust dietary and nutrition preferences, browse a ranked shortlist, save meals, and refine the request through another message. Meals is a separate full-catalog grid for browsing and saved items.

The app supports two chat modes. The explicitly labeled Demo mode uses the curated meal catalog and deterministic rules in `lib/meals.ts`, `lib/recommender.ts`, and `lib/demo-chat.ts`; it is useful without credentials. Connected mode uses the installed Pi catalog (`@earendil-works/pi-ai` and `@earendil-works/pi-agent-core`) through the app's server routes. The model can help interpret a request, while meal results still come from the app's catalog and constraints. This is a focused Pi Agent integration, not the full coding-agent CLI.

There is no MCP connection in this app. The server does not persist conversations, preferences, saved meals, or API keys. A connected provider key is held in an in-memory session behind an HTTP-only cookie for eight hours. The session store is process-local, so a multi-instance deployment needs a shared session design before it can support reconnects across instances.

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

From `SystemCode/remymy`, the shared Compose file can build only the app while skipping its currently scaffolded service dependencies:

```bash
docker compose --env-file .env.nonprod up --build --no-deps app
```

## Product behavior

- Discover opens with a short welcome in the chat composer; once the user sends a request, it becomes an active conversation with bounded recommendations alongside the response.
- Meals remains a separate full grid for catalog browsing, meal details, and locally saved meals.
- Plain-language requests are interpreted into dietary, time, cuisine, exclusion, and goal preferences.
- Results are filtered by hard constraints first, then ranked by query matches and preference fit.
- Each result includes short reasons so the ordering is understandable. “Surprise me” rotates the eligible shortlist without relaxing constraints.
- A later message is interpreted together with the current preferences, allowing the user to narrow the shortlist. Use Your preferences to explicitly remove a restriction or increase the time limit. Saving preferences clears the prior query so it cannot reapply old constraints.
- Provider and model choices are populated from the Pi model catalog exposed by the server, so the UI does not maintain a second model list. Demo mode is available when no provider key is configured; connected mode validates the provider and model before opening an eight-hour session.
- Saved meals and the active preference state are kept in this browser's local storage. They are not synchronized between browsers or persisted to a server, and chat history is discarded when the page or server session ends.

The catalog is curated demo data. Cooking times, calories, protein values, ingredients, and tags are illustrative estimates and are not verified against USDA FoodData Central, a dietitian, or a live nutrition service. The app should not be used as medical or allergy advice; users should verify ingredients and nutrition for their own needs.

## Project layout

```text
app/             Next.js route and global styles
components/      Interactive meal cards, composer, preferences, and detail UI
lib/meals.ts     Curated meal records and preference defaults
lib/recommender.ts  Request interpretation and deterministic ranking
lib/recommender.test.ts  Behavioral tests for the recommendation contract
lib/demo-chat.ts  Deterministic local chat reply helper
lib/server/      Pi catalog, request guards, and in-memory connection sessions
```

The repository also contains separate `mcp`, `models`, and database service scaffolding under `SystemCode/remymy`. Those services are outside this app's focused Pi integration and are not required for local Demo mode. Connecting them later will require an explicit integration layer and a reviewed data contract; the UI currently does not assume they are available.

## Data and privacy boundary

No account, analytics, database, or MCP service is required to run the app. Preference changes and saved meals remain in this browser's local storage. Connected API keys are accepted by the server only to create an ephemeral, process-local session and are never included in the public provider/model catalog or connection status. Do not enter sensitive health information.

The automated suite uses mocked provider and agent behavior and does not make real model calls. A live connected response has not been verified here because no provider key is available; treat a successful local test run as a contract check rather than proof of provider credentials, network access, or deployment behavior.

## Verification

See [TESTING.md](./TESTING.md) for the automated and hands-on browser checks.
