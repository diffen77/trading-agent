#!/bin/bash
BASE_URL=${1:-https://trading.lediff.se}
FAILED=0

echo "🔥 Smoke test: $BASE_URL"
echo "================================"

check_url() {
    local url="$1"
    local response=$(curl -s -o /dev/null -w "%{http_code}" "$url")
    if [ "$response" -eq 200 ]; then
        echo -e "\e[32m✅\e[0m $url"
    else
        echo -e "\e[31m❌\e[0m $url (HTTP $response)"
        FAILED=1
    fi
}

check_json() {
    local url="$1"
    local response=$(curl -s "$url")
    if echo "$response" | jq empty &> /dev/null; then
        echo -e "\e[32m✅\e[0m $url (valid JSON)"
    else
        echo -e "\e[31m❌\e[0m $url (invalid JSON)"
        FAILED=1
    fi
}

check_url "$BASE_URL"
check_url "$BASE_URL/api/portfolio"
check_json "$BASE_URL/api/portfolio"
check_url "$BASE_URL/api/stocks"
check_json "$BASE_URL/api/stocks"

echo "================================"
if [ "$FAILED" -eq 0 ]; then
    echo -e "\e[32m🎉 All checks passed!\e[0m"
    exit 0
else
    echo -e "\e[31m💀 Some checks failed!\e[0m"
    exit 1
fi
