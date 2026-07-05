#!/bin/bash

# ============================================
# STRIPE TEAM PRODUCTS SETUP
# ============================================

# --- TEAM 10 ---
TEAM10_PRODUCT_ID=$(stripe products create \
  --name="Team 10" \
  --description="Team plan for up to 10 users" \
  -d "metadata[type]=team" \
  -d "metadata[max_seats]=10" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "✅ Created Team 10 product: $TEAM10_PRODUCT_ID"

TEAM10_PRICE_ID=$(stripe prices create \
  -d "product=$TEAM10_PRODUCT_ID" \
  -d "unit_amount=22900" \
  -d "currency=eur" \
  -d "recurring[interval]=month" \
  -d "nickname=Team 10 Monthly" \
  -d "metadata[max_seats]=10" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "✅ Created Team 10 price: $TEAM10_PRICE_ID (229€/month)"

# --- TEAM 20 ---
TEAM20_PRODUCT_ID=$(stripe products create \
  --name="Team 20" \
  --description="Team plan for up to 20 users" \
  -d "metadata[type]=team" \
  -d "metadata[max_seats]=20" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "✅ Created Team 20 product: $TEAM20_PRODUCT_ID"

TEAM20_PRICE_ID=$(stripe prices create \
  -d "product=$TEAM20_PRODUCT_ID" \
  -d "unit_amount=37900" \
  -d "currency=eur" \
  -d "recurring[interval]=month" \
  -d "nickname=Team 20 Monthly" \
  -d "metadata[max_seats]=20" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "✅ Created Team 20 price: $TEAM20_PRICE_ID (379€/month)"

# --- TEAM 50 ---
TEAM50_PRODUCT_ID=$(stripe products create \
  --name="Team 50" \
  --description="Team plan for up to 50 users" \
  -d "metadata[type]=team" \
  -d "metadata[max_seats]=50" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "✅ Created Team 50 product: $TEAM50_PRODUCT_ID"

TEAM50_PRICE_ID=$(stripe prices create \
  -d "product=$TEAM50_PRODUCT_ID" \
  -d "unit_amount=82900" \
  -d "currency=eur" \
  -d "recurring[interval]=month" \
  -d "nickname=Team 50 Monthly" \
  -d "metadata[max_seats]=50" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "✅ Created Team 50 price: $TEAM50_PRICE_ID (829€/month)"

# ============================================
# SUMMARY
# ============================================
echo ""
echo "============================================"
echo "CREATED PRODUCTS & PRICES"
echo "============================================"
echo "Team 10 | Product: $TEAM10_PRODUCT_ID | Price: $TEAM10_PRICE_ID | 229€/month"
echo "Team 20 | Product: $TEAM20_PRODUCT_ID | Price: $TEAM20_PRICE_ID | 379€/month"
echo "Team 50 | Product: $TEAM50_PRODUCT_ID | Price: $TEAM50_PRICE_ID | 829€/month"
echo ""
echo "============================================"
echo "Add these to your .env:"
echo "============================================"
echo "STRIPE_PRICE_TEAM_10=$TEAM10_PRICE_ID"
echo "STRIPE_PRICE_TEAM_20=$TEAM20_PRICE_ID"
echo "STRIPE_PRICE_TEAM_50=$TEAM50_PRICE_ID"