#!/bin/bash

# ContractOS Canary Promotion Script
# Usage: ./promote-canary.sh [deployment_id]

set -e

DEPLOYMENT_ID=${1}
API_URL="${API_URL:-http://localhost:8000}"
TOKEN="${TOKEN:-}"

if [ -z "$DEPLOYMENT_ID" ]; then
    echo "Usage: ./promote-canary.sh [deployment_id]"
    exit 1
fi

echo "🚀 Promoting canary deployment: $DEPLOYMENT_ID"

# Check deployment health first
echo "📊 Checking canary health..."
HEALTH=$(curl -s -H "Authorization: Bearer $TOKEN" \
    "$API_URL/api/v1/canary/deployments/$DEPLOYMENT_ID/health")

HEALTH_STATUS=$(echo $HEALTH | jq -r '.status')

if [ "$HEALTH_STATUS" != "healthy" ]; then
    echo "❌ Canary is not healthy (status: $HEALTH_STATUS)"
    echo "Issues:"
    echo $HEALTH | jq -r '.issues[]'
    exit 1
fi

echo "✅ Canary is healthy"

# Get comparison
echo "📈 Comparing versions..."
COMPARISON=$(curl -s -H "Authorization: Bearer $TOKEN" \
    "$API_URL/api/v1/canary/deployments/$DEPLOYMENT_ID/compare")

RECOMMENDATION=$(echo $COMPARISON | jq -r '.recommendation')

if [ "$RECOMMENDATION" == "rollback" ]; then
    echo "❌ Recommendation is to rollback"
    exit 1
fi

echo "✅ Recommendation: $RECOMMENDATION"

# Promote canary
echo "🎯 Promoting canary..."
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
    "$API_URL/api/v1/canary/deployments/$DEPLOYMENT_ID/promote" | jq .

echo ""
echo "🎉 Canary promoted successfully!"
echo ""
echo "Next steps:"
echo "1. Monitor the deployment for 15 minutes"
echo "2. Update the stable version image tag"
echo "3. Remove the canary deployment"
