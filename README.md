# s3-dedup-engine
Engineering Thesis @ WUT

## Repository Layout

- `services/gateway` - Go gateway CLI (dedup chain of responsibility)
- `services/ai_worker` - Python gRPC similarity stub
- `proto` - protobuf contracts (`dedup.proto`)
- `infra/docker` - local docker compose stack
- `infra/localstack/init` - LocalStack bootstrap scripts
- `infra/terraform` - IaC placeholder for cloud provisioning
- `scripts` - helper scripts
- `tests` - integration and e2e tests

## Run Locally (Docker + LocalStack)

From repository root:

```bash
docker compose -f infra/docker/docker-compose.yml up --build -d
```

Check services:

```bash
docker compose -f infra/docker/docker-compose.yml ps
```

Inspect LocalStack resources:

```bash
docker exec -it s3-dedup-localstack awslocal s3 ls
docker exec -it s3-dedup-localstack awslocal dynamodb list-tables
```

Stop stack:

```bash
docker compose -f infra/docker/docker-compose.yml down -v
```

## Run Gateway on Host (against compose)

With LocalStack + ai_worker up:

```bash
cd services/gateway
go run ./cmd/gateway
```

Defaults: LocalStack `http://localhost:4566`, AI worker `localhost:50051`, bucket `raw-tickets-bucket`, table `ticket-hashes`.

## Soft-Deduplication Flow

1. `ChunkPipeline` → CDC mock, continue
2. `HashCollisionPipeline` → SHA-256; collision ⇒ metadata-only S3 upload
3. `SimilarityPipeline` → gRPC score; below threshold ⇒ full upload; at/above ⇒ `InfoGainPipeline`
4. `InfoGainPipeline` → high gain ⇒ full upload; low gain ⇒ metadata-only (drop heavy payload)
