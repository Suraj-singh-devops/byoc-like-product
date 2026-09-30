import type { Permission } from "./types";

// Mirrors OPERATION_PERMISSIONS in backend/app/domain/rbac.py: cancelling or retrying an
// operation needs operation:manage plus the permission of the operation's own type.
const OPERATION_PERMISSION: Record<string, Permission> = {
  CREATE_CLUSTER: "cluster:create",
  SCALE_CLUSTER: "cluster:scale",
  DELETE_CLUSTER: "cluster:delete",
  HEALTH_CHECK: "cluster:health_check",
};

export function canManageOperation(can: (permission: Permission) => boolean, operationType: string): boolean {
  return can("operation:manage") && can(OPERATION_PERMISSION[operationType] ?? "cluster:delete");
}
