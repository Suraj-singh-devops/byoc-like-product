# syntax=docker/dockerfile:1.7
# Container image of the agent for local development and CI. On database VMs the agent runs
# as a systemd service installed by the bootstrap script (see docs/agent.md).
FROM golang:1.27-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY cmd ./cmd
COPY internal ./internal
ARG VERSION=0.1.0
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -X main.version=${VERSION}" -o /out/byoc-agent ./cmd/byoc-agent

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/byoc-agent /byoc-agent
ENTRYPOINT ["/byoc-agent"]
CMD ["-config", "/etc/byoc/agent.json"]
