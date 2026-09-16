#!/bin/bash

# ContractOS Canary Rollback Script
# Usage: ./rollback-canary.sh [deployment_id]

set -e

DEPLOYMENT_ID=${1}
API_URL="${API_URL:-http://localhost:8000}"
TOKEN="${TOKEN:-}"

if [ -z "$DEPLOYMENT_ID" ]; then
    echo "Usage: ./rollback-canary.sh [deployment_id]"
    exit 1
fi

echo "⚠️  Rolling back canary deployment: $DEPLOYMENT_ID"

# Get deployment info first
echo "📊 Getting deployment info..."
DEPLOYMENT=$(curl -s -H "Authorization: Bearer $TOKEN" \
    "$API_URL/api/v1/canary/deployments/$DEPLOYMENT_ID")

CURRENT_TRAFFIC=$(echo $DEPLOYMENT | jq -r '.traffic_percentage')
CANARY_VERSION=$(echo $DEPLOYMENT | jq -r '.canary_version')
STABLE_VERSION=$(echo $DEPLOYMENT | jq -r '.current_version')

echo "   Canary version: $CANARY_VERSION"
echo "   Stable version: $STABLE_VERSION"
echo "   Current traffic: $CURRENT_TRAFFIC%"

# Confirm rollback
read -p "Are you sure you want to rollback? (y/n): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Rollback cancelled"
    exit 0
fi

# Perform rollback
echo "🔄 Rolling back..."
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
    "$API_URL/api/v1/canary/deployments/$DEPLOYMENT_ID/rollback" | jq .

echo ""
echo "✅ Canary rolled back successfully!"
echo ""
echo "The canary deployment has been stopped and all traffic is now on the stable version."
