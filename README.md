# s3-dedup-engine
Engineering Thesis @ WUT — Visual Data Deduplication Gateway

## Repository Layout

- `services/gateway` - Go gateway CLI (visual dedup chain of responsibility)
- `services/ai_worker` - Python gRPC worker (SSCD embeddings, quality score, pgvector)
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

## AI Worker Logging

The worker logs through `rich.logging.RichHandler`, giving aligned time/level/message/path columns, automatic highlighting of numbers and paths, and syntax-highlighted tracebacks. Configure it with environment variables:

| variable | default | effect |
| --- | --- | --- |
| `LOG_LEVEL` | `INFO` | root log level; set `DEBUG` for neighbor lookups and model loading |
| `LOG_FORCE_COLOR` | unset | set to `1` to keep ANSI color when stderr is piped, as under `docker compose logs` |
| `LOG_TRACEBACK_LOCALS` | unset | set to `1` to include local variables in tracebacks |

Two deliberate choices in [services/ai_worker/logger.py](services/ai_worker/logger.py): console markup is disabled, because image keys are attacker-controlled and a filename like `photo[1].jpg` would otherwise be parsed as a Rich markup tag; and traceback locals are off by default, since locals in this service hold whole image buffers.

## Tests

```bash
cd services/ai_worker
uv run python -m pytest
```

Shared fixtures and test doubles live in [services/ai_worker/tests/conftest.py](services/ai_worker/tests/conftest.py): deterministic multi-octave-noise JPEGs at several resolutions, a fake psycopg cursor that records SQL and replays queued rows, a fake SSCD TorchScript module, a repository double, and a gRPC servicer context that captures aborts. No test touches the network, Postgres, or the real model weights.

## Typing

The worker is fully typed with no `Any` annotations. That requires real stubs rather than treating generated code as dynamic:

- `--pyi_out` generates `pb/dedup_pb2.pyi`, so `ImageChunk` and `ProcessImageResponse` are concrete types
- `grpc-stubs` supplies `grpc.Server`, `grpc.ServicerContext` and `grpc.StatusCode`
- `types-protobuf` supplies the `google.protobuf` stubs that `dedup_pb2.pyi` imports
- Only `pb.dedup_pb2_grpc` is still skipped by mypy, since the generated service code is unannotated

Because the message types are real, tests construct genuine `dedup_pb2.ImageChunk` values instead of stand-ins.

Stateful collaborators are dataclasses rather than hand-written `__init__` bodies. `EmbeddingRepository` and `QualityScorer` are frozen, while `SscdEmbedder` and `AIGrpcServer` keep their lazily built internals in `field(init=False)` slots. Optional constructor arguments are expressed as `field(default_factory=...)`, so `AIGrpcServer()` reaches for the shared singletons and `EmbeddingRepository()` resolves its DSN from the environment without any `Optional` juggling.

Values that identify the class rather than the instance are annotated `ClassVar`, which also keeps dataclasses from mistaking them for fields. The canonical values live in [services/ai_worker/const/](services/ai_worker/const/), split by domain (`grpc`, `log`, `sscd`, `quality`, `pg`); each class binds the ones it owns, such as `QualityScorer.RESOLUTION_WEIGHT` and `AIGrpcServer.OPTIONS`. The SQL builders and scoring helpers are `classmethod`s reading those attributes, so a subclass can retarget a table or re-weight a score without editing the queries.

## Server Lifecycle

[services/ai_worker/main.py](services/ai_worker/main.py) wraps the gRPC server in a context manager built on `contextlib.ExitStack`:

```python
with AIGrpcServer() as server:
    server.start()
    shutdown.wait()
```

`__enter__` initializes the schema, warms the model, enters a `ThreadPoolExecutor`, binds the port and registers the stop callback, then calls `ExitStack.pop_all()` to transfer ownership. Anything that raises during setup unwinds immediately, so a failed schema init cannot leave a thread pool behind. Teardown runs last-in-first-out: the server stops with a grace period before the pool is joined.

`SIGINT` and `SIGTERM` are routed to a `threading.Event` rather than killing the process, otherwise `docker stop` would bypass the teardown entirely. The container exits with code 0 on a graceful stop.

## SQL Construction

Every statement is a frozen dataclass under [services/ai_worker/sql.py](services/ai_worker/sql.py). `SQLQuery` is the abstract base; each concrete class (`ClosestStoredEmbeddingQuery`, `LockQualityScoreQuery`, `UpsertEmbeddingQuery`, `PromoteEmbeddingQuery`, and the schema queries) implements `build()`. The repository executes them as `cur.execute(ClosestStoredEmbeddingQuery().sql, params)`.

Values are never inlined into the SQL. `pypika.Parameter` emits a bare `%s` or `%(vector)s::vector` that passes through the builder unquoted, so psycopg still does the binding and a hostile image key cannot reach the parser. `tests/test_sql.py` asserts that no runtime statement contains a quoted literal.

pgvector's `<=>` operator is not a PyPika builtin. `CosineDistance` wraps a two-line `Comparator` enum in a `BasicCriterion`, which makes the distance a first-class term reusable in both the select list and the `ORDER BY`.

Stock `Query.create_index` has no `USING` clause or operator class. `HnswIndexBuilder` extends PyPika's `CreateIndexBuilder` with `.using()` and `.ops()`, so `CreateHnswIndexQuery` still goes through the builder. `CREATE EXTENSION` remains a one-line statement because PyPika has no extension API.

One wart worth knowing: `PostgreSQLQuery.into()` is annotated as returning the *base* `QueryBuilder`, so the Postgres-only `on_conflict` resolves through `Selectable.__getattr__` and type-checks as a `Field`. A single `cast(PostgreSQLQueryBuilder, ...)` in `UpsertEmbeddingQuery` restores the chain.

## Visual Soft-Deduplication Flow

1. `FileNameMatchPipeline` — DynamoDB `FILE#` lookup; record the existing S3 key
2. `ChunkPipeline` — CDC first chunk SHA-256; probe `CHUNK#`
3. `FullHashPipeline` — if chunk candidates exist, full SHA-256; bit-identical hit → soft-dedup (skips the AI worker entirely)
4. `SimilarityPipeline` — terminal stage; streams the image to the AI worker in 2 MiB chunks and maps the returned status

The AI worker spools the stream to disk past 20 MiB, computes a 512-dim SSCD descriptor and a quality score, then resolves the verdict inside one pgvector transaction:

| status | condition | gateway action |
| --- | --- | --- |
| `inserted` | nearest cosine distance > 0.10 | upload payload |
| `replaced` | near-duplicate with a higher quality score | upload payload, overwrite the incumbent |
| `duplicate_rejected` | near-duplicate with an equal or lower quality score | metadata only |

Upload rules: always write metadata JSON; stream the image only when `SoftDedup == false` (overwrites the S3 key when `ReplaceExisting`).

## Quality Score (Information Gain)

The score decides which of two near-duplicates survives. It is a weighted sum of three normalized factors:

- **Resolution**, weight 0.5 — total pixels against a 4K baseline, read from the original image *before* any decode downscaling
- **Sharpness**, weight 0.3 — Laplacian variance measured on a fixed 512 px canvas so it stays comparable across resolutions
- **Contrast**, weight 0.2 — standard deviation of pixel intensities

Sharpness must be measured on a fixed canvas: Laplacian variance is not scale-invariant and *rises* as an image shrinks, so a native-resolution measurement would reward thumbnails over their own originals.

## Regenerate Protobuf Stubs

```bash
protoc --proto_path=proto \
  --go_out=services/gateway/pb --go_opt=paths=source_relative \
  --go-grpc_out=services/gateway/pb --go-grpc_opt=paths=source_relative \
  proto/dedup.proto

cd services/ai_worker
uv run python -m grpc_tools.protoc --proto_path=../../proto \
  --python_out=pb --grpc_python_out=pb ../../proto/dedup.proto
```

The generated `pb/dedup_pb2_grpc.py` imports `dedup_pb2` as a top-level module; change it to `from . import dedup_pb2 as dedup__pb2` so the `pb` package stays importable.
