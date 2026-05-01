FROM golang:1.24-alpine AS builder
WORKDIR /app

COPY go.mod ./
RUN go mod download

COPY . .
RUN go build -o /out/gateway ./main.go

FROM alpine:3.20
WORKDIR /app
COPY --from=builder /out/gateway /app/gateway

EXPOSE 8080
ENTRYPOINT ["/app/gateway"]
