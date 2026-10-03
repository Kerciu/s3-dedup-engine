# s3-dedup-engine
Engineering Thesis @ WUT — Visual Data Deduplication Gateway

## Repository Layout

- `services/gateway` - Go gateway CLI (visual dedup chain of responsibility)
- `services/ai_worker` - Python gRPC similarity mock (image bytes in / score out)
- `proto` - protobuf contracts (`dedup.proto`)
- `infra/docker` - local docker compose stack
- `infra/localstack/init` - LocalStack bootstrap scripts

## Run Locally (Docker + LocalStack)

```bash
docker compose -f infra/docker/docker-compose.yml up --build -d
```

Recreate LocalStack volumes after schema changes:

```bash
docker compose -f infra/docker/docker-compose.yml down -v
docker compose -f infra/docker/docker-compose.yml up --build -d
```

## Run Gateway on Host

With LocalStack + ai_worker up:

```bash
cd services/gateway
go run ./cmd/gateway --image ./path/to/photo.jpg
go run ./cmd/gateway --image ./path/to/photo.jpg --verbose
```

Defaults: LocalStack `http://localhost:4566`, AI worker `localhost:50051`. Use `--verbose` for debug-level pipeline details (hashes, chunk sizes, Dynamo lookups).

## Visual Soft-Deduplication Flow

1. `FileNameMatchPipeline` — DynamoDB `FILE#` lookup; annotate prior metadata
2. `ChunkPipeline` — CDC first chunk SHA-256; probe `CHUNK#`
3. `FullHashPipeline` — if chunk candidates exist, full SHA-256; bit-identical hit → soft-dedup (skip AI)
4. `SimilarityPipeline` — otherwise gRPC visual score (mock returns 0.0 without reference)
5. `InfoGainPipeline` — score ≥ 0.95 compares resolution/size → replace or soft-dedup

Upload rules: always write metadata JSON; stream the image only when `SoftDedup == false` (overwrites S3 key when `ReplaceExisting`).
