"use client";

import { useFeatureFlags } from "@/contexts/FeatureFlagContext";

/**
 * Hook to check if a feature flag is enabled
 * @param flagName - The name of the feature flag
 * @returns boolean indicating if the flag is enabled
 */
export function useFeatureFlag(flagName: string): boolean {
  const { isEnabled, isLoading } = useFeatureFlags();
  
  if (isLoading) {
    return false;
  }
  
  return isEnabled(flagName);
}

/**
 * Hook to get all feature flags
 * @returns Object with all flags and helper functions
 */
export function useAllFeatureFlags() {
  return useFeatureFlags();
}
