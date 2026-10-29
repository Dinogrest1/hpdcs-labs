# syntax=docker/dockerfile:1
FROM golang:1.23-alpine AS build
WORKDIR /src
COPY go.mod main.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /loadgen .

FROM scratch
COPY --from=build /loadgen /loadgen
ENTRYPOINT ["/loadgen"]
