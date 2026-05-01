# s3-dedup-engine
Engineering Thesis @ WUT

## Repository Layout

- `services/gateway` - Go gateway service
- `services/ai_worker` - Python AI worker service
- `proto` - protobuf contracts
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
