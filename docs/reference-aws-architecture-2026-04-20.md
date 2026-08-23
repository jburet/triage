# AWS Global architecture at 2026\-04\-20

# Zeenea Architecture Overview 

**Date:** 2026-04-20 

## 1. System Overview

Zeenea is a SaaS data catalog platform deployed 100% on AWS across 7 accounts and 5+ regions. The system comprises 14 application services (9 core + public-api, scim-api, mcp-server, agent-studio-steward, chrome-plugin-v2), a tenant management control plane, 5 Lambda functions (3 Platform-invoked: sql-to-lineage, powerquery-to-lineage, sql-keys-identifier; 1 token refresher; 1 DB migration), an analytics data pipeline, 3 infrastructure-as-code repositories, and a shared deployment tool. 20 repos in the repository map below; 25 total in the deployer whitelist.

### 1.1 Repository Map

| Repository | Role | Tech Stack | Tenancy Model | Deployment Method |
| --- | --- | --- | --- | --- |
| `zeenea-api-gateway` | **Internet-facing reverse proxy** | Java 17/21 / Spring Cloud Gateway (WebFlux) | N/A (routing layer) | EKS via `application-deployer` |
| `studio` | Data catalog UI (BFF + frontend) | Java 21 / Spring Boot + Angular 21 | Multi-tenant (shared pod) | EKS via `application-deployer` |
| `socialdataexplorer` | Social data exploration | Scala 2.13 / Play 3.0 + Angular 21 | Multi-tenant (shared pod) | EKS via `application-deployer` |
| `graphql-api` | Public GraphQL API | Node.js 24 / NestJS + GraphQL | Multi-tenant (shared pod) | EKS via `application-deployer` |
| `platform` | Core catalog engine | Scala 2.13 / Pekko HTTP + gRPC | Mono-tenant (StatefulSet per tenant) | EKS StatefulSet via `platform-infra` |
| `description-api` | Translation + description service | Kotlin / Quarkus / Java 17 | Multi-tenant (shared pod) | EKS via `application-deployer` |
| `file-backend` | Image storage for rich text editor | Kotlin 1.9 / Quarkus / Java 17 | Multi-tenant (shared pod) | EKS via `application-deployer` |
| `admin` | Admin UI for tenant management | Java / Spring Boot + Angular | Multi-tenant (shared pod) | EKS via `application-deployer` |
| `tenant-management-2` | Tenant lifecycle control plane | Java 21 / Spring Boot 3.5 + Vite/Lit frontend | N/A (internal tool) | EKS via `application-deployer` (Helm) |
| `sql-to-lineage` | SQL lineage inference Lambda | Python 3.13 / FastAPI + sqllineage | N/A (serverless function) | Lambda via `application-deployer` |
| `landing-aws-data` | Analytics data pipeline | Terraform + Glue + Airflow + Redshift | N/A (analytics platform) | `application-deployer` + Makefile |
| `landing-auth0` | Auth0 IaC configuration | Terraform (Auth0 + AWS providers) | N/A (identity config) | GitHub Actions (manual dispatch) |
| `zeenea-infra` | Landing zone IaC | Terragrunt + 51 Terraform modules | N/A (infrastructure) | Multi-account AWS Org |
| `platform-infra` | Platform IaC + deploy | Terraform (layered) + Makefile | Per-tenant provisioning | GitHub Actions + Makefile |
| `public-api` | Customer-facing REST API | Java 17 / Quarkus 3.5 | Multi-tenant (shared pod) | EKS via `application-deployer` |
| `scim-api` | SCIM identity provisioning | Java 17 / Quarkus 3.3 | Multi-tenant (shared pod) | EKS via `application-deployer` |
| `datacatalog-mcp-server-py` | MCP server for AI tools | Python 3.12 / FastMCP + Hono | Multi-tenant (shared pod) | EKS via `application-deployer` |
| `agent-studio-steward` | AI chat agent for Studio | TypeScript / Node.js 24 / Mastra + Hono | Multi-tenant (shared pod) | EKS via `application-deployer` |
| `chrome-plugin-v2` | Chrome extension + NestJS BFF | TypeScript / React (Plasmo) + NestJS 11 | N/A (browser extension) | EKS via `application-deployer` |
| `application-deployer` | Shared deployment tool | GitHub Actions composite action (v54) | N/A (CI/CD tooling) | Referenced by 25 app repos |

### 1.2 Application Architecture

```
                              Internet
                                 │
                    ┌────────────▼──────────────────────────────────────┐
                    │             zeenea-api-gateway                     │
                    │  (Spring Cloud Gateway — ONLY public endpoint)     │
                    │  Tenant detection, routing, IP filter, WAF         │
                    └──┬────────┬────────┬────────┬────────┬───────────┘
                       │        │        │        │        │
              ┌────────▼──┐┌───▼────┐┌──▼─────┐┌─▼──────┐│  Also routes to:
              │  Studio   ││Explorer││GraphQL ││Platform││  ├── description-api
              │ (Java/SB) ││(Scala/ ││  API   ││(Scala/ ││  ├── file-backend
              │ BFF +     ││ Play)  ││(NestJS)││ Pekko) ││  ├── admin
              │ Angular   ││BFF +   ││BFF     ││gRPC +  ││  ├── public-api
              │           ││Angular ││        ││REST    ││  ├── scim-api
              └─────┬─────┘└───┬────┘└───┬────┘└───┬────┘│  ├── mcp-server
                    │          │         │         │     │  └── ~10 others
                    └──────────┴────┬────┴─────────┘     │
                                    │                    │
              ┌─────────────────────▼────────────────────┘
              │
              │         ┌──────────────────┐  ┌───────────────────┐
              ├────────→│ description-api   │  │  file-backend      │
              │         │ (Kotlin/Quarkus)  │  │  (Kotlin/Quarkus)  │
              │         │ AWS Translate+KMS │  │  S3 image storage  │
              │         └──────────────────┘  └───────────────────┘
              │
              │     ┌────────────────────────────────────────────┐
              │     │            Shared Services                  │
              ├────→│  Auth0 (JWT) | Unleash (flags) | OTEL      │
              │     └──────┬──────────────┬──────────────┬───────┘
              │            │              │              │
              │ ┌──────────▼──┐    ┌──────▼──────┐ ┌────▼──────────────┐
              │ │ PostgreSQL  │    │  Solr 9     │ │ OrientDB           │
              │ │ (RDS Aurora)│    │ (search)    │ │ (EBS vol per tenant│
              │ │ + pgvector  │    │             │ │  NOT phased out    │
              │ └─────────────┘    └─────────────┘ │  before migration) │
              │                                     └──────────────────┘
              │
              │  ┌──────────────────────────────────────────────────────┐
              │  │  tenant-management-2 (internal control plane)         │
              └─→│  Spring Boot 3.5 + Vite/Lit frontend                  │
                 │  Manages tenant lifecycle → triggers platform-infra   │
                 │  via GitHub API repository_dispatch                    │
                 └──────────────────────────────────────────────────────┘

              ┌──────────────────────────────────────────────────────┐
              │  sql-to-lineage + 2 other Lambdas                    │
              │  Python 3.13 / FastAPI — invoked by Platform         │
              │  Lineage inference + SQL keys identification          │
              └──────────────────────────────────────────────────────┘

              ┌──────────────────────────────────────────────────────┐
              │  landing-aws-data (analytics pipeline)               │
              │  Glue ETL → Redshift → Studio (customer metrics)    │
              │  + Metabase (internal CS dashboards)                  │
              └──────────────────────────────────────────────────────┘
```

### 1.3 Inter-Service Communication

| From | To | Protocol | Purpose |
| --- | --- | --- | --- |
| Internet | **API Gateway** | HTTPS | **Only public entry point** — tenant detection, routing, IP filter |
| API Gateway | Studio | HTTP (internal) | Reverse proxy after tenant resolution |
| API Gateway | Explorer | HTTP (internal) | Reverse proxy after tenant resolution |
| API Gateway | GraphQL API | HTTP (internal) | Reverse proxy after tenant resolution |
| API Gateway | Platform | HTTP (internal) | Reverse proxy after tenant resolution |
| API Gateway | file-backend | HTTP (internal) | Image upload/download for rich text editor |
| API Gateway | \~15 other backends | HTTP (internal) | Routes to admin, scim, mcp-server, etc. |
| Studio / Explorer | file-backend | HTTP (via Gateway) | Rich text editor image storage |
| API Gateway | tenant-management-2 | gRPC | Tenant existence check (via `TenantExistenceCheckFilter`) |
| GraphQL API | Platform | HTTP REST | BFF — translates GraphQL to Platform REST API |
| Studio | Platform | HTTP REST | Tenant data operations |
| Explorer | Platform | HTTP REST | Data exploration queries |
| Explorer | description-api | HTTP (port 8090) | Description translation + terminology |
| description-api | AWS Translate | AWS SDK | Text/document translation, per-tenant terminology |
| description-api | KMS | AWS SDK | Terminology encryption (per-tenant key) |
| Platform | Lambda functions (x3) | AWS SDK invoke | Lineage inference (sql-to-lineage, powerquery-to-lineage, sql-keys-identifier) — each Lambda uses shared M2M token for Platform callbacks |
| Platform | SQS | AWS SDK polling | Data pipeline ingestion |
| Platform | Bedrock | AWS SDK (LangChain4j → migrating to LiteLLM) | AI embeddings + chat |
| tenant-management-2 | platform-infra (GitHub) | GitHub API `repository_dispatch` | Triggers Terraform deploy/destroy for tenants |
| tenant-management-2 | Auth0 | HTTPS | Tenant Auth0 configuration |
| tenant-management-2 | S3 | AWS SDK | Analytics data persistence |
| All apps | Auth0 | HTTPS | JWT authentication |
| All apps (except GraphQL API) | Secrets Manager | AWS SDK | Credential retrieval |
| Gateway, Studio, Explorer, Platform | S3 | AWS SDK | White-labeling assets |

---

## 2. AWS Account Structure

```
AWS Organization
├── root (438850682587)           Management account
│   ├── Organizations & SCPs
│   ├── SSO / Identity Center
│   └── Cost management
│
├── infra (097607883991)          Shared infrastructure
│   ├── ECR repositories (all app images)
│   ├── GitHub Actions runners (EKS-hosted)
│   ├── Terraform state (S3 + DynamoDB)
│   ├── Datadog secrets
│   └── CI/CD IAM roles
│
├── dev (969329306878)            Development
├── sandbox (279967583215)        Sandbox / experimentation
├── integration (121864946425)    Integration testing
├── staging (219548626310)        Staging
├── preprod (306170271398)        Pre-production
└── prod (612651702340)           Production
    ├── EU zone (eu-west-3)
    ├── US zone (us-east-1)
    └── UK zone (eu-west-2)
```

---

## 3. Multi-Region Deployment Model

### 3.1 Zone Architecture

| Zone | Primary Region | DR Region | VPC CIDR Pattern |
| --- | --- | --- | --- |
| EU | eu-west-3 (Paris) | eu-central-1 (Frankfurt) | 10.X0.0.0/16 |
| US | us-east-1 (Virginia) | us-east-2 (Ohio) | 10.X1.0.0/16 |
| UK | eu-west-2 (London) | — | 10.X2.0.0/16 |

Where X varies by account (3=prod, 2=preprod, 6=dev, etc.)

### 3.2 Per-Zone Infrastructure

Each zone gets independently provisioned:

- VPC with public/private subnets (3+ AZs)
- EKS cluster
- RDS Aurora PostgreSQL cluster
- NAT Gateways
- VPC peering to infra account

### 3.3 VPC CIDR Allocation (Production)

```
prod-eu:  10.30.0.0/16, 10.34.0.0/16 (eu-west-3)
prod-us:  10.31.0.0/16, 10.33.0.0/16 (us-east-1)
prod-uk:  10.32.0.0/16               (eu-west-2)
```

---

## 4. Tenant Isolation Models

### 4.1 Multi-Tenant Applications (Studio, Explorer)

- Single Kubernetes Deployment per environment/region
- All tenants share same pod(s)
- Tenant isolation at application layer (Auth0 JWT tenant claim)
- Shared RDS database with tenant column/schema
- Horizontal scaling via replica count

### 4.2 Mono-Tenant Application (Platform)

- One Kubernetes StatefulSet per tenant
- **Dedicated EBS volume per tenant** — stores OrientDB data (graph DB, embedded in pod)
- **OrientDB → PostgreSQL migration in progress but NOT complete before sovereign cloud work**
- OrientDB requires persistent block storage attached to pod — **hard dependency on EBS or equivalent**
- **AWS Backup** manages OrientDB data backup via EBS snapshots (configured in zeenea-infra)
- Dedicated database (per-tenant DB in shared Aurora cluster) for PostgreSQL data
- Per-tenant resource allocation (3 performance profiles: high/middle/low)
- Per-tenant configuration (40+ parameters including AI provider, SFTP, custom domain)
- Tenant lifecycle managed via platform-infra Terraform workspaces

---

## 5. Data Architecture

### 5.1 Database Topology

```
                    ┌──────────────────────────────────────┐
                    │    RDS Aurora PostgreSQL Cluster      │
                    │    (per zone, shared across apps)     │
                    ├──────────────────────────────────────┤
                    │  studio DB        (multi-tenant)     │
                    │  explorer DB      (multi-tenant)     │
                    │  tenant_A DB      (platform)         │
                    │  tenant_B DB      (platform)         │
                    │  tenant_C DB      (platform)         │
                    │  ...              (one per tenant)    │
                    └──────────────────────────────────────┘

                    ┌──────────────────────────────────────┐
                    │  Redshift Cluster                     │
                    │  (usage analytics — queried by Studio)│
                    └──────────────────────────────────────┘

                    ┌──────────────────────────────────────┐
                    │  OrientDB (embedded in platform pod)  │
                    │  Data stored on EBS volume per tenant │
                    │  Backed up via AWS Backup (EBS snaps) │
                    │  Migration to PG in progress but      │
                    │  NOT complete — STAYS IN SCOPE         │
                    └──────────────────────────────────────┘

                    ┌──────────────────────────────────────┐
                    │  Apache Solr 9 (search engine)        │
                    │  Managed by Zeenea, deployed via      │
                    │  ArgoCD on K8s — cloud-agnostic       │
                    │  No AWS dependency, no migration      │
                    │  needed for sovereign cloud           │
                    └──────────────────────────────────────┘
```

### 5.2 Storage (S3 Buckets)

| Bucket Pattern | Used By | Purpose |
| --- | --- | --- |
| `zeenea-data-pipelines-{env}-{region}` | Studio | Watch-list exports |
| `zeenea-white-labeling-*` | All apps | Tenant branding assets |
| `zeenea-artifacts` | CI/CD | Build artifacts, OTEL extensions |
| Per-tenant buckets | Platform | Import/export, data sampling |
| `state-{account_id}` | Terraform | Remote state files |
| `zeenea-tfstates` | application-deployer | Centralized TF state |

### 5.3 Message Queues

| Queue/Stream | Producer | Consumer | Purpose |
| --- | --- | --- | --- |
| SQS (per-tenant) | S3 events / external | Platform | Data pipeline ingestion |
| SQS (PII ML) | Platform | ML pipeline | PII detection |
| SQS (suggestions) | Platform | ML pipeline | Link suggestions |
| Kinesis "ask-ai" | Explorer | landing-aws-data (Firehose→S3) | AI search feedback analytics |
| Kinesis (auth0 events) | Auth0 | landing-aws-data (Firehose→S3) | Auth0 event streaming |
| SNS topics | Data pipeline | SQS subscriptions | Event fan-out |

### 5.4 Analytics Platform (landing-aws-data) — SEPARATE SYSTEM

Complete AWS-native analytics pipeline serving **both** internal CS metrics **and** customer-facing dashboards (adoption rate metrics in Studio):

```
Data Sources                    ETL                          Storage              Consumers
─────────────                   ───                          ───────              ─────────
Auth0 events ──→ Kinesis ──→ Firehose ──→ S3 (raw)
Auth0 users  ──→ Airflow DAG ──────────→ S3 (raw)     ┌→ Glue Crawlers
Platform data ─→ DataPipelineRepository ─→ S3 (raw) ──┤  (catalog S3 data)
Ask-AI feedback → Kinesis Firehose ────→ S3 (raw)     └→ Glue ETL Jobs (14x)
                                                           │
                                                      S3 (gold zone)
                                                           │
                                           ┌───────────────┼───────────────┐
                                           ▼               ▼               ▼
                                      Athena           Redshift         Metabase
                                    (SQL on S3)     (Spectrum +      (ECS Fargate)
                                                    local tables)    → CS dashboards
                                                         │
                                              ┌──────────┴──────────┐
                                              ▼                     ▼
                                    Studio queries           Metabase queries
                                  (RedshiftDataAsync)      (internal CS)
                                  → CUSTOMER-FACING
                                    adoption rate metrics
```

**Redshift serves two audiences:**

1. **Customer-facing:** Studio queries adoption rate metrics, usage analytics via `RedshiftDataAsyncClient` — shown to customers in Studio UI
2. **Internal:** Metabase dashboards for CS team — optional (`enable_internal_analytics` flag)

**AWS services (ALL cloud-specific):** Kinesis, Firehose, S3, Glue, Athena, Redshift, MWAA (Airflow), ECS Fargate, SageMaker, Lambda, KMS, Secrets Manager, SNS, DynamoDB

**Migration impact — HIGHER than initially assessed:**

- Redshift **cannot be deferred** if sovereign cloud customers need adoption metrics in Studio
- Must provide analytics warehouse replacement for customer-facing data
- Options: ClickHouse, PostgreSQL (materialized views), Snowflake, or DuckDB
- Studio's `RedshiftDataAsyncClient` abstraction needs replacement regardless

### 5.5 Data Pipeline Flow (Platform Ingestion — separate from analytics)

```
External data source
  → S3 upload (per-tenant import bucket)
  → S3 event notification → SNS → SQS (per-tenant)
  → Platform polls SQS (DataPipelineSqsWatcher, 10s interval)
  → Download .json.gz from S3 via S3TransferManager
  → Route by filename:
      ├── pii.json.gz → PiiMlIngestionService
      └── item_links.json.gz → GeneratedItemLinkSuggestionIngestionService
  → Store in PostgreSQL / OrientDB
  → Index in Solr

Also: Platform exports analytics data back to S3 (DataPipelineRepository)
  → S3 → SNS → Glue ETL (landing-aws-data) → Redshift
  → See section 5.4 for complete bidirectional flow
```

---

## 6. Security Architecture

### 6.1 Authentication & Authorization

| Layer | Mechanism |
| --- | --- |
| User authentication | Auth0 (JWT, OIDC) |
| Service-to-service | gRPC + JWT / M2M tokens (via Lambda) |
| AWS resource access | IAM Roles for Service Accounts (IRSA) |
| CI/CD to AWS | GitHub Actions OIDC federation |
| Secrets | AWS Secrets Manager |
| Encryption at rest | KMS (per-region keys with DR replicas) |
| Encryption in transit | TLS (ACM certificates) |
| Network | VPC isolation, security groups, VPC peering |
| Web protection | AWS WAF (Studio) |
| Threat detection | GuardDuty |
| Compliance | SecurityHub |
| Access control | AWS SSO + StrongDM (3rd party) |

### 6.2 Auth0 Configuration (landing-auth0)

Auth0 is managed as IaC via Terraform Auth0 provider. Repo: `landing-auth0`.

**Auth0 Resources Managed:**

- **Tenant:** Branding (universal login, colors, locales: en/fr/de)
- **Applications:**
    - `zeenea` — SPA client (main user-facing app)
    - `zeenea-api` — M2M client (non-interactive, client\_credentials)
    - `zeenea-customers-native-app` — Native app client
    - `sql-keys-identifier` — M2M client for Lambda
    - `sql-to-lineage` — M2M client for Lambda
    - `studio` — M2M client for Studio
- **API/Resource Server:** `https://api.{env}.zeenea.app` (public API), `https://platforms.auth0.{env}.zeenea.local` (cross-app auth)
- **Custom Domain:** `login.{env}.zeenea.app` (auth0\_managed\_certs)
- **Email Provider:** AWS SES (eu-west-3) for transactional emails
- **Email Templates:** Verify email, change password (i18n: en/fr)
- **Rules:** Enrich access token (add user email + tenant to JWT)
- **Hooks:** Pre-user-registration check
- **Log Streams:** Auth0 → AWS EventBridge (→ landing-aws-data Kinesis)

**AWS coupling points:**

- **Route53:** DNS CNAME for Auth0 custom domain verification
- **SES:** Email provider for Auth0 transactional emails
- **Secrets Manager:** M2M client credentials stored at `auth0/{app_name}`
- **EventBridge:** Auth0 log stream → AWS EventBridge (infra account + env account)

**Migration note:** Auth0 itself is SaaS (cloud-agnostic). Self-hosted identity (Keycloak) is an option to analyze and plan for sovereign requirements. Auth0 config has AWS dependencies:

1. Route53 → any DNS provider can verify custom domain
2. SES → Auth0 can use its own email or another SMTP provider
3. Secrets Manager → Vault or env vars
4. EventBridge log stream → webhook or alternative stream target
5. GitHub secrets store Auth0 credentials (manual, not in AWS)

### 6.3 Secrets Distribution

```
AWS Secrets Manager paths:
├── /shared-db/{env}/{region}/{cluster}/{app-or-tenant}
│   └── db, user, password
├── landing/{env}/components/auth0
│   └── clientId, apiClientId, apiClientSecret
├── auth0/sql-to-lineage
│   └── client_id, client_secret, audience, domain (created by landing-auth0)
├── auth0/sql-keys-identifier
│   └── client_id, client_secret, audience, domain (created by landing-auth0)
├── auth0/studio
│   └── client_id, client_secret, audience, domain (created by landing-auth0)
├── landing/{env}/components/unleash
│   └── clientSecret
├── landing/{env}/components/datadog
│   └── apiKey, appKey
├── landing/{env}/platform/hubspot-access-key
├── landing/{env}/platform/openai-access-key
└── landing/{env}/platform/langsmith-api-key
```

---

## 7. Observability Stack

| Layer | Tool | Cloud-Specific? |
| --- | --- | --- |
| APM / Traces | OpenTelemetry → Datadog | OTEL is portable, Datadog is SaaS |
| Metrics | Datadog Agent | SaaS (replaceable) |
| Logs | CloudWatch → Datadog | CloudWatch is AWS-specific |
| Synthetics | Datadog Synthetics | SaaS |
| Profiling | Pyroscope (optional) | Open-source |
| Frontend RUM | Datadog RUM | SaaS |
| Alerting | Datadog → Slack | SaaS |

**OTEL instrumentation** is already in place across all apps (Java agent v1.33.4), making observability migration straightforward — only collector/backend needs to change. Self-hosted observability (e.g., Grafana/Prometheus/Loki) is an option to analyze and plan for sovereign deployments.

---

## 8. AI / ML Architecture

| Component | Service | Cloud-Specific? | Used By |
| --- | --- | --- | --- |
| LLM Chat | AWS Bedrock (currently LangChain4j, migrating to LangGraph + LiteLLM) | Yes → No (after migration) | Platform |
| Embeddings | AWS Bedrock Titan (migrating to LiteLLM abstraction) | Yes → No (after migration) | Platform |
| AI Guardrails | AWS Bedrock guardrails (zeenea-infra) | Yes | Platform |
| Vector Store | PostgreSQL pgvector | No | Platform |
| **Translation** | **AWS Translate** | **Yes** | **description-api** |
| **Terminology Mgmt** | **AWS Translate + KMS** | **Yes** | **description-api** |
| Lineage Inference | Custom Lambda (sql-to-lineage) — Python/FastAPI | Yes (code is portable) | Platform |
| Feature Gating | Unleash (self-hosted) | No | All apps |

**Key observations:**

- Platform has `DisabledTextToEmbedding` and `DisabledQuestionAnswer` fallbacks — AI features can be disabled per tenant. LangChain4j abstraction layer already supports multiple providers.
- **AWS Translate** in description-api has **no drop-in open-source replacement**. **Translation is mandatory** for sovereign cloud customers. Per-tenant custom terminology is NOT widely used (simplifies migration). **Strategy:** per-deployment provider — GCP deployments use Google LLM, sovereign cloud deployments use on-premise LLM.
- Lineage Lambdas already have FastAPI endpoints — containerization confirmed feasible.
- **Platform AI migration in progress:** LangChain4j functionality being migrated to LangGraph agents with LiteLLM abstraction (ETA: next month). Once complete, Platform will use LiteLLM like agent-studio-steward — fully provider-agnostic.
- **LiteLLM** already used by agent-studio-steward. Agents only aware of LiteLLM, not underlying provider. Supports all cloud providers.

---

## 9. Disaster Recovery

| Component | Strategy | RPO/RTO | Cloud-Specific? |
| --- | --- | --- | --- |
| RDS Aurora | AWS Backup policy, cross-region replicas | **24h / 24h** | Yes |
| **OrientDB (EBS)** | **AWS Backup → EBS snapshots per tenant** | **24h / 24h** | **Yes — hard dependency** |
| Platform data | EBS volume snapshots (managed by AwsEbsSnapshotService) | **24h / 24h** | Yes |
| Terraform state | S3 versioning + encryption | Low risk | Yes |
| Container images | ECR replication | Low risk | Yes |
| KMS keys | Multi-region replica keys | Automatic | Yes |
| DNS | Route53 (global) | N/A | Yes |

**DR targets (confirmed):**

- **All services (Platform, Studio, Explorer, Analytics):** RPO 24h / RTO 24h
- **Strategy:** Active/passive failover (NOT active-active)
- **DR drills:** Executed once per year. Recovery runbooks exist.

**OrientDB backup policy (confirmed):**

- Frequency: 1 backup/day
- Retention: 30 days
- Cross-region copy: Yes
- Application-triggered snapshots: Before OrientDB database migrations (via `AwsEbsSnapshotService`)
- Restore: Managed by Terraform in platform-infra
- Cross-region replication: Not needed — backup/restore only

**DRP regions configured:** eu-central-1 (for EU), us-east-2 (for US). UK (eu-west-2) has no DR region deployed. Controlled by `IS_DRP` flag in Terragrunt.

**Critical for migration:** OrientDB backup relies on EBS snapshots via AWS Backup (zeenea-infra `aws_backup` module). Cloud-agnostic alternative must provide:

1. Persistent block storage attached to StatefulSet pods (e.g., Longhorn, Rook-Ceph, cloud-native PV)
2. Backup mechanism for block volumes (e.g., Velero with CSI snapshots, restic) — daily backup, 30-day retention
3. Cross-region backup copy for DR (active/passive failover)

---

## 10. Glossary

| Term | Definition |
| --- | --- |
| Landing zone | Base AWS infrastructure (VPC, EKS, RDS) per account/zone |
| Zone | Geographic deployment unit (EU, US, UK) |
| IRSA | IAM Roles for Service Accounts — EKS pod → IAM role mapping |
| Mono-tenant | One application instance per customer |
| Multi-tenant | Shared application instance, tenant isolation in code |
| application-deployer | GitHub Actions composite action for standardized Terraform deployments |
| Terragrunt | Terraform wrapper for DRY multi-account/region configurations |
