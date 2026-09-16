"use client";

import { useEffect, useState, useCallback } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { getMyPermissions } from "@/lib/api";

interface UsePermissionsReturn {
  permissions: string[];
  loading: boolean;
  error: string | null;
  hasPermission: (permission: string) => boolean;
  canView: boolean;
  canComment: boolean;
  canProposeChanges: boolean;
  canApprove: boolean;
  canSign: boolean;
  canManageParticipants: boolean;
  refresh: () => void;
}

export function usePermissions(agreementId: string | null): UsePermissionsReturn {
  const { token } = useAuth();
  const [permissions, setPermissions] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchPermissions = useCallback(async () => {
    if (!token || !agreementId) {
      setPermissions([]);
      setLoading(false);
      return;
    }

    try {
      setLoading(true);
      setError(null);
      const result = await getMyPermissions(token, agreementId);
      setPermissions(result.permissions);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch permissions");
      setPermissions([]);
    } finally {
      setLoading(false);
    }
  }, [token, agreementId]);

  useEffect(() => {
    let active = true;
    queueMicrotask(() => {
      if (active) fetchPermissions();
    });
    return () => {
      active = false;
    };
  }, [fetchPermissions]);

  const hasPermission = useCallback(
    (permission: string) => permissions.includes(permission),
    [permissions]
  );

  return {
    permissions,
    loading,
    error,
    hasPermission,
    canView: hasPermission("agreement.view"),
    canComment: hasPermission("agreement.comment"),
    canProposeChanges: hasPermission("agreement.propose_change"),
    canApprove: hasPermission("agreement.approve"),
    canSign: hasPermission("agreement.sign"),
    canManageParticipants: hasPermission("agreement.manage_participants"),
    refresh: fetchPermissions,
  };
}
