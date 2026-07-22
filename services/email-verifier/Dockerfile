FROM golang:1.22.12-alpine3.21 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/email-verifier ./cmd/server

FROM build AS test
RUN apk add --no-cache build-base && go vet ./... && CGO_ENABLED=1 go test -race ./...

FROM alpine:3.21.3
RUN addgroup -S verifier && adduser -S -G verifier verifier && apk add --no-cache ca-certificates
USER verifier
COPY --from=build /out/email-verifier /usr/local/bin/email-verifier
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 CMD wget -qO- http://127.0.0.1:8080/health >/dev/null || exit 1
ENTRYPOINT ["/usr/local/bin/email-verifier"]
