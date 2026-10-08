# Remymy MCP gateway

This is the small Python MCP service used by the Remymy web app. It exposes
three tools over stateless Streamable HTTP:

- `say_hello(name: str = "there")` — returns a friendly greeting.
- `recommend_meals(preferences, query, limit)` — filters the mounted Food.com
  recipe dataset, then ranks matches with the model service. The chat app uses
  this MCP tool for every connected meal recommendation.
- `rank_foodcom_meals(candidate_recipe_ids, user_id, content_scores, limit)` —
  calls the model service for a hybrid collaborative/content ranking. Callers
  must apply hard dietary and time constraints before invoking this tool.

`recommend_meals` screens diet constraints before model ranking: vegan recipes
must have the source vegan tag and pass an animal-ingredient check; vegetarian
recipes must be source-tagged and pass a meat/seafood check. Halal requests are
limited to vegan-tagged recipes and screened for animal ingredients, pork,
alcohol, and gelatin terms. The Food.com data has no halal certification, so
these are not certified halal meals; verify source ingredients and
cross-contamination.

The MCP endpoint is `http://localhost:8000/mcp` when the service is running
locally. The health endpoint is `http://localhost:8000/health`.

## Run locally

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python main.py
```

The Docker Compose app is configured to call the same endpoint at
`http://mcp:8000/mcp`.
