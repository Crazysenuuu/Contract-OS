"use client";

import { ReactNode } from "react";
import { useFeatureFlag } from "@/hooks/useFeatureFlag";

interface FeatureFlagProps {
  name: string;
  children: ReactNode;
  fallback?: ReactNode;
}

/**
 * Component to conditionally render content based on feature flag
 * @example
 * <FeatureFlag name="advanced_analytics">
 *   <AdvancedAnalyticsPanel />
 * </FeatureFlag>
 * 
 * @example
 * <FeatureFlag name="new_feature" fallback={<OldFeature />}>
 *   <NewFeature />
 * </FeatureFlag>
 */
export function FeatureFlag({ name, children, fallback = null }: FeatureFlagProps) {
  const isEnabled = useFeatureFlag(name);
  
  if (isEnabled) {
    return <>{children}</>;
  }
  
  return <>{fallback}</>;
}

/**
 * Component to hide content when feature flag is enabled (for kill switches)
 */
export function FeatureFlagHide({ name, children }: { name: string; children: ReactNode }) {
  const isEnabled = useFeatureFlag(name);
  
  if (!isEnabled) {
    return <>{children}</>;
  }
  
  return null;
}
