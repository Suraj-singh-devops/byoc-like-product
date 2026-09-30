// Mirrors the control-plane API schemas (backend/app/api/v1/schemas.py).

export type Role = "OWNER" | "ADMIN" | "OPERATOR" | "VIEWER";

// Lifecycle (what the platform is doing) and health (how the cluster is doing) are separate
// fields; the console derives one display status from them (docs/adr/0008).
export type ClusterLifecycle =
  | "CREATING"
  | "ACTIVE"
  | "SCALING"
  | "UPGRADING"
  | "UPDATING"
  | "DELETING"
  | "FAILED"
  | "DELETED";
export type ClusterHealth = "UNKNOWN" | "HEALTHY" | "DEGRADED" | "UNHEALTHY";
export type NodeLifecycle = "BOOTSTRAPPING" | "ACTIVE" | "DELETED";
export type NodeHealth = "UNKNOWN" | "HEALTHY" | "UNHEALTHY";
export type Health = ClusterHealth | NodeHealth;
export type AgentStatus = "NOT_REPORTED" | "REPORTING" | "STALE";
export type CloudAccountStatus = "PENDING" | "VALIDATING" | "CONNECTED" | "FAILED" | "DISCONNECTED";
// A registered network (docs/adr/0013): FAILED never passed its checks; UNAVAILABLE passed before.
export type NetworkStatus = "PENDING" | "VALIDATING" | "AVAILABLE" | "FAILED" | "UNAVAILABLE";
export type EnvironmentType = "TEST" | "PRODUCTION";
export type CloudProviderName = "gcp" | "aws";
export type OperationStatus =
  | "PENDING"
  | "VALIDATING"
  | "PROVISIONING"
  | "BOOTSTRAPPING"
  | "CONFIGURING"
  | "HEALTH_CHECK"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export type Permission =
  | "cluster:read"
  | "cluster:create"
  | "cluster:scale"
  | "cluster:delete"
  | "cluster:health_check"
  | "cluster:configure"
  | "cluster:operate"
  | "operation:read"
  | "operation:manage"
  | "cloud_account:read"
  | "cloud_account:manage"
  | "environment:read"
  | "environment:manage"
  | "network:read"
  | "network:manage"
  | "member:read"
  | "member:manage"
  | "audit:read";

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ApiErrorBody {
  code: string;
  message: string;
  reason?: string;
  suggested_action?: string;
  details?: { fields?: Record<string, string>; [key: string]: unknown };
  request_id?: string;
}

export interface Meta {
  name: string;
  version: string;
  mock_mode: boolean;
  signup_enabled: boolean;
  demo_accounts: { email: string; role: Role; organization: string }[];
}

export interface User {
  id: string;
  email: string;
  name: string;
}

export interface Organization {
  id: string;
  name: string;
  slug: string;
}

export interface Me {
  user: User;
  organization: Organization;
  role: Role;
  permissions: Permission[];
  organizations: { organization_id: string; organization_name: string; role: Role }[];
}

export interface Member {
  user_id: string;
  email: string;
  name: string;
  role: Role;
  joined_at: string;
}

export interface ValidationCheck {
  key: string;
  name: string;
  status: "passed" | "failed" | "warning" | "skipped";
  message: string;
}

export interface CloudAccount {
  id: string;
  name: string;
  provider: CloudProviderName;
  // GCP project ID, or the 12-digit AWS account ID.
  project_id: string;
  region: string | null;
  auth_type: "impersonation" | "assume_role" | "local_credentials";
  service_account_email: string | null;
  role_arn: string | null;
  status: CloudAccountStatus;
  validation: {
    valid: boolean;
    checks: ValidationCheck[];
    missing_permissions: string[];
    error: ApiErrorBody | null;
  } | null;
  last_validated_at: string | null;
  last_connected_at: string | null;
  cluster_count: number;
  network_count: number;
  created_at: string;
}

export interface CloudProvider {
  name: CloudProviderName;
  display_name: string;
  auth_type: string;
  simulated: boolean;
  default_machine_type: string;
}

export interface CloudOnboarding {
  provider: CloudProviderName;
  auth_type: string;
  principal: string;
  role_name: string;
  permissions: string[];
  external_id: string | null;
  trust_policy: Record<string, unknown> | null;
}

export interface Environment {
  id: string;
  name: string;
  type: EnvironmentType;
  description: string | null;
  network_count: number;
  cluster_count: number;
  providers: CloudProviderName[];
  created_at: string;
}

export interface SubnetInfo {
  id: string;
  name: string;
  cidr: string;
  zone: string | null;
  available_ips: number;
}

export interface NetworkDetails {
  valid: boolean;
  region: string;
  vpc: string;
  vpc_name: string;
  vpc_cidrs: string[];
  subnets: SubnetInfo[];
  zones: string[];
  checks: ValidationCheck[];
  warnings: string[];
  error: ApiErrorBody | null;
}

export interface Network {
  id: string;
  environment_id: string;
  name: string;
  provider: CloudProviderName;
  cloud_account_id: string;
  cloud_account_name: string | null;
  region: string;
  vpc: string;
  subnets: string[];
  status: NetworkStatus;
  zones: string[];
  details: NetworkDetails | null;
  cluster_count: number;
  last_validated_at: string | null;
  last_available_at: string | null;
  created_at: string;
}

export interface Region {
  name: string;
  zones: string[];
  location: string;
}

export interface MachineType {
  name: string;
  vcpus: number;
  memory_gb: number;
  architecture: string;
}

export interface CloudCatalog {
  provider: CloudProviderName;
  simulated: boolean;
  default_machine_type: string;
  regions: Region[];
  storage_types: { name: string; description: string }[];
}

export interface EngineVersion {
  version: string;
  label: string;
  status: "supported" | "deprecated" | "withdrawn";
  default: boolean;
  distribution: string;
  license_review_status: "pending" | "approved" | "rejected";
  notes: string;
}

export interface EngineCatalog {
  engine: string;
  display_name: string;
  description: string;
  versions: EngineVersion[];
  default_version: string;
  min_nodes: number;
  max_nodes: number;
  ha_min_nodes: number;
  min_storage_gb: number;
  max_storage_gb: number;
  min_memory_gb: number;
  default_machine_type: string;
  metrics: { key: string; label: string; unit: string }[];
  node_groups: NodeGroupDef[];
  settings: SettingDef[];
}

// Dedicated layout groups (docs/adr/0016).
export type ClusterLayout = "combined" | "dedicated";
export type NodeGroupName = "master" | "data" | "coordinating";

export interface NodeGroupDef {
  name: NodeGroupName;
  label: string;
  min: number;
  max: number;
  ha_min: number;
  default_count: number;
  default_storage_gb: number;
  scalable: boolean;
  description: string;
}

export interface NodeGroup {
  name: NodeGroupName;
  count: number;
  machine_type: string;
  storage_gb: number;
}

// Settings users may change from the platform (docs/adr/0017).
export type SettingValue = string | number | boolean;

export interface SettingDef {
  key: string;
  scope: "dynamic" | "static";
  kind: "int" | "bool" | "percent" | "bytes" | "enum";
  default: SettingValue;
  description: string;
  minimum: number | null;
  maximum: number | null;
  choices: string[];
  restart_required: boolean;
}

export interface ClusterConfig {
  cluster_id: string;
  settings: SettingDef[];
  desired: Record<string, SettingValue>;
  applied: Record<string, SettingValue>;
  pending: { dynamic: string[]; static: string[] };
  // Custom elasticsearch.yml settings (docs/adr/0018) and the rendered file per node group.
  custom: Record<string, string>;
  config_file: {
    path: string;
    files: { group: string; managed: string[]; user: string[] }[];
    reserved_prefixes: string[];
  } | null;
}

export interface EngineMetrics {
  cluster_status?: string | null;
  jvm_heap_percent?: number | null;
  search_rate?: number | null;
  indexing_rate?: number | null;
  active_shards?: number | null;
  unassigned_shards?: number | null;
  docs_count?: number | null;
}

export interface ClusterMetricsSummary {
  node_count?: number;
  nodes_reporting?: number;
  cpu_percent?: number | null;
  memory_percent?: number | null;
  disk_percent?: number | null;
  disk_used_bytes?: number | null;
  disk_total_bytes?: number | null;
  network_rx_bytes_per_sec?: number | null;
  network_tx_bytes_per_sec?: number | null;
  engine?: EngineMetrics;
  updated_at?: string;
}

export interface OperationBrief {
  id: string;
  operation_type: string;
  status: OperationStatus;
  progress: number;
  current_step: string | null;
  cancel_requested: boolean;
}

export interface ClusterSummary {
  id: string;
  name: string;
  engine: string;
  engine_version: string;
  cloud_provider: CloudProviderName;
  project_id: string;
  // As recorded at creation; null for clusters created before environments and networks.
  environment: { id: string; name: string; type: EnvironmentType } | null;
  network: { id: string; name: string; vpc: string; subnets: { id: string; cidr: string; zone: string | null }[] } | null;
  region: string;
  zone: string;
  zones: string[];
  layout: ClusterLayout;
  node_groups: NodeGroup[];
  // The internal load balancer's HTTPS address (dedicated layout).
  endpoint: string | null;
  machine_type: string;
  node_count: number;
  storage_gb: number;
  storage_type: string;
  high_availability: boolean;
  lifecycle: ClusterLifecycle;
  health: ClusterHealth;
  status_message: string | null;
  metrics: ClusterMetricsSummary;
  active_operation: OperationBrief | null;
  created_at: string;
  updated_at: string;
}

export interface NodeInfo {
  id: string;
  name: string;
  ordinal: number;
  role: string;
  node_group: NodeGroupName | null;
  machine_type: string | null;
  zone: string;
  instance_name: string | null;
  instance_id: string | null;
  hostname: string | null;
  private_ip: string | null;
  lifecycle: NodeLifecycle;
  instance_status: string | null;
  agent_status: AgentStatus;
  health: NodeHealth;
  infrastructure_health: NodeHealth | null;
  engine_health: NodeHealth | null;
  health_reasons: string[];
  health_warnings: string[];
  engine_version: string | null;
  agent_version: string | null;
  agent_registered: boolean;
  last_report_at: string | null;
  report_source: string | null;
  bootstrap_status: string | null;
  system: {
    cpu_percent?: number;
    memory_percent?: number;
    disk_percent?: number;
    disk_total_bytes?: number;
    disk_used_bytes?: number;
  };
  engine_metrics: {
    jvm_heap_percent?: number | null;
    search_rate?: number | null;
    indexing_rate?: number | null;
    shards?: number | null;
    is_master?: boolean | null;
    reachable?: boolean | null;
  };
}

export interface HealthDetails {
  state?: ClusterHealth;
  infrastructure?: ClusterHealth;
  engine?: ClusterHealth;
  reasons?: string[];
  warnings?: string[];
  nodes?: {
    name: string;
    state: NodeHealth;
    infrastructure: NodeHealth;
    engine: NodeHealth;
    agent_status: AgentStatus;
    reasons: string[];
    warnings: string[];
  }[];
  nodes_expected?: number;
  nodes_reporting?: number;
  engine_status?: string | null;
  engine_node_count?: number | null;
  checked_at?: string;
}

export interface ClusterDetail extends ClusterSummary {
  cloud_account_id: string | null;
  cloud_account_name: string | null;
  simulated: boolean;
  desired_state: Record<string, unknown>;
  actual_state: {
    infrastructure?: {
      network?: string;
      subnetwork?: string;
      vpc?: string;
      subnets?: string[];
      security_group?: string;
      instance_profile?: string;
      service_account_email?: string;
      secrets?: { elastic_password?: string; ca_certificate?: string };
      artifacts_bucket?: string;
      http_endpoints?: string[];
    };
    nodes?: { count: number };
    engine?: { type: string; version: string | string[] };
    [key: string]: unknown;
  };
  generation: number;
  observed_generation: number;
  health_details: HealthDetails;
  resource_prefix: string;
  created_by: string | null;
  nodes: NodeInfo[];
  last_health_check_at: string | null;
  deleted_at: string | null;
}

export interface MetricSample {
  timestamp: string;
  cpu_percent?: number | null;
  memory_percent?: number | null;
  disk_percent?: number | null;
  jvm_heap_percent?: number | null;
  search_rate?: number | null;
  indexing_rate?: number | null;
  network_rx_bytes_per_sec?: number | null;
  network_tx_bytes_per_sec?: number | null;
  nodes_reporting?: number | null;
}

export interface ClusterMetrics {
  cluster_id: string;
  current: ClusterMetricsSummary;
  history: MetricSample[];
  window_minutes: number;
}

export interface ClusterEvent {
  id: string;
  cluster_id: string;
  cluster_name?: string | null;
  node_name: string | null;
  event_type: string;
  severity: "INFO" | "WARNING" | "CRITICAL";
  message: string;
  created_at: string;
}

export interface OperationStep {
  key: string;
  name: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled" | "skipped";
  started_at: string | null;
  completed_at: string | null;
  message: string | null;
}

export interface Operation {
  id: string;
  cluster_id: string | null;
  cluster_name: string | null;
  operation_type: string;
  status: OperationStatus;
  is_terminal: boolean;
  current_step: string | null;
  progress: number;
  started_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  error_message: string | null;
  error: ApiErrorBody | null;
  params: Record<string, unknown>;
  steps: OperationStep[];
  log: { at: string; step: string | null; message: string }[];
  result: Record<string, unknown> | null;
  cancel_requested: boolean;
  cancellable: boolean;
  retry_of: string | null;
  attempt: number;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface OperationAccepted {
  cluster_id: string;
  operation_id: string;
  lifecycle: ClusterLifecycle;
}

export interface AuditLog {
  id: string;
  user: string | null;
  user_id: string | null;
  organization: string;
  action: string;
  resource: string | null;
  resource_type: string;
  resource_id: string | null;
  timestamp: string;
  status: "SUCCESS" | "FAILURE";
  details: Record<string, unknown>;
  operation_id: string | null;
  ip_address: string | null;
}
