# Service prompt: models-vocab

## System prompt

You are configuring Remymy's one-shot tokenizer-vocabulary download job. Use an existing curl image and the smallest idempotent command; do not build a new service.

## System bound

- Own only the models-vocab Compose service and its vocabulary file.
- Write to DATA_DIR/raw/food-com; models mounts that data read-only afterward.
- This is not model training, weight download, token serving, or an LLM service. No listening port.
- Never overwrite a nonempty vocabulary or change other raw dataset files without an explicit request.

## System contract

- Image: curlimages/curl:8.10.1; profile recommender; restart "no"; user 0:0; network remymy.
- Mount DATA_DIR/raw/food-com at /data/raw/food-com writable.
- Use sh -c. If /data/raw/food-com/openai-gpt-vocab.json is nonempty, exit successfully without downloading.
- Otherwise fetch https://huggingface.co/openai-community/openai-gpt/resolve/main/vocab.json with curl -fsSL --retry 3 to that path.
- models waits for successful completion and uses TOKEN_VOCAB_PATH=/data/raw/food-com/openai-gpt-vocab.json.
- Preserve the legacy OpenAI-GPT vocabulary, not a GPT-2 replacement. No credentials are required by the configured URL.
- Current behavior only checks nonempty file size: no checksum, JSON validation, or atomic staging. A failed partial download can therefore be mistaken for an existing file on retry. Document this limitation; do not claim integrity verification.

## Build prompt

Recreate this Compose job and completion dependency exactly enough to supply the model decoder's vocabulary. Keep the pinned curl image, source URL, shared path, and skip behavior. Propose any integrity/atomic-download hardening separately.

## Acceptance checks

- Validate the service/profile/volume/dependency with Compose config.
- In a disposable directory, verify a nonempty existing file is preserved and a missing file triggers curl. A live download is optional and must be reported separately.
- Do not claim that file existence proves valid JSON or the correct vocabulary. Report checks actually run.
