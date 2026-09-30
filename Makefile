.PHONY: up down logs ps test test-backend test-backend-postgres test-agent test-terraform test-platform test-helm test-frontend lint agent smoke clean

up: ## Build and start the local control plane (mock mode) on http://localhost:3000
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f backend worker frontend

ps:
	docker compose ps

test: test-backend test-agent test-terraform test-platform test-helm test-frontend ## Run every test suite

test-backend: ## Unit, API, provider and integration tests (SQLite)
	cd backend && uv run pytest

test-backend-postgres: ## The same suite against PostgreSQL (needs TEST_DATABASE_URL)
	cd backend && TEST_DATABASE_URL=$${TEST_DATABASE_URL:?set TEST_DATABASE_URL} uv run pytest

test-agent: ## Go agent tests with the race detector (in Docker)
	docker run --rm -v "$(CURDIR)/agent/elasticsearch-agent":/src -w /src golang:1.27 go test -race ./...

test-terraform: ## Terraform validation and plan tests with mocked providers
	cd infrastructure/terraform/gcp/modules/elasticsearch && terraform init -backend=false -input=false >/dev/null && terraform validate && terraform test

test-platform: ## GKE platform Terraform: validate + plan tests with mocked providers
	cd infrastructure/terraform/platform && terraform init -backend=false -input=false >/dev/null && terraform validate && terraform test

test-helm: ## Helm chart: lint, render for GKE and plain Kubernetes, validate against the Kubernetes schemas
	helm lint deploy/helm/database-platform
	helm lint deploy/helm/database-platform -f deploy/helm/database-platform/ci/gke-values.yaml
	for values in ci/local-values.yaml ci/gke-values.yaml; do \
	  helm template t deploy/helm/database-platform -f deploy/helm/database-platform/$$values | \
	  docker run --rm -i ghcr.io/yannh/kubeconform:latest -strict -summary -kubernetes-version 1.33.0 \
	    -schema-location default \
	    -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' - || exit 1; \
	done

test-frontend: ## Type-check and build the console (in Docker, sources copied into the container)
	docker run --rm -v "$(CURDIR)/frontend":/src:ro node:24-alpine sh -c \
	  'mkdir /app && cd /src && tar --exclude=node_modules --exclude=.next -cf - . | tar -xf - -C /app && cd /app && npm ci --no-audit --no-fund --loglevel=error && npx tsc --noEmit && npx next build'

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .
	terraform fmt -recursive -check infrastructure/terraform

agent: ## Build linux agent binaries into agent/elasticsearch-agent/dist
	docker run --rm -v "$(CURDIR)/agent/elasticsearch-agent":/src -w /src -e CGO_ENABLED=0 golang:1.27 sh -c \
	  'for arch in amd64 arm64; do GOOS=linux GOARCH=$$arch go build -trimpath -ldflags "-s -w -X main.version=0.1.0" -o dist/byoc-agent-linux-$$arch ./cmd/byoc-agent; done'

smoke: ## Walk the acceptance flow and the v2 rules against the running stack
	python3 tests/e2e/acceptance_test.py

clean:
	docker compose down -v
