FROM golang:1.26-alpine AS builder
WORKDIR /app

COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN go build -o /out/gateway ./cmd/gateway

FROM alpine:3.20
WORKDIR /app
COPY --from=builder /out/gateway /app/gateway

EXPOSE 8080
ENTRYPOINT ["/app/gateway"]
