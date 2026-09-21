#!/bin/bash
# test-api.sh - Script de prueba automatizado para Expense Reconciler MVP
# Uso: ./test-api.sh

set -e

BASE_URL="http://localhost:8000/api/v1"
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}===========================================${NC}"
echo -e "${YELLOW}  Expense Reconciler MVP - Test Script${NC}"
echo -e "${YELLOW}===========================================${NC}"
echo ""

# Verificar que la API esté corriendo
echo -e "${YELLOW}1. Health Check...${NC}"
if curl -s "$BASE_URL/../health" | grep -q "healthy"; then
    echo -e "${GREEN}✓ API corriendo${NC}"
else
    echo -e "${RED}✗ API no responde. Ejecuta: docker-compose up${NC}"
    exit 1
fi

# 1. Crear Empresa
echo -e "\n${YELLOW}2. Crear Empresa...${NC}"
COMPANY_RESPONSE=$(curl -s -X POST "$BASE_URL/companies/" \
  -H "Content-Type: application/json" \
  -d '{"name":"Mi Taquería Test","tax_id":"MTA123456789"}')

COMPANY_ID=$(echo "$COMPANY_RESPONSE" | jq -r '.id')
echo -e "${GREEN}✓ Empresa creada: $COMPANY_ID${NC}"
echo "$COMPANY_RESPONSE" | jq .

# 2. Crear Tickets manuales
echo -e "\n${YELLOW}3. Crear Tickets...${NC}"

TICKET1=$(curl -s -X POST "$BASE_URL/tickets/" \
  -H "Content-Type: application/json" \
  -d "{\"company_id\":\"$COMPANY_ID\",\"provider_name\":\"GAS PROVEEDOR SA\",\"provider_tax_id\":\"GPS010101XXX\",\"total_amount\":\"500.00\",\"tax_amount\":\"80.00\",\"expense_date\":\"2025-01-15\",\"category\":\"SUMINISTROS\"}")

TICKET1_ID=$(echo "$TICKET1" | jq -r '.id')
echo -e "${GREEN}✓ Ticket 1: $TICKET1_ID${NC}"

TICKET2=$(curl -s -X POST "$BASE_URL/tickets/" \
  -H "Content-Type: application/json" \
  -d "{\"company_id\":\"$COMPANY_ID\",\"provider_name\":\"REFRESCOS MEXICO\",\"provider_tax_id\":\"REF010101XXX\",\"total_amount\":\"1250.00\",\"tax_amount\":\"200.00\",\"expense_date\":\"2025-01-16\",\"category\":\"MERCANCIA\"}")

TICKET2_ID=$(echo "$TICKET2" | jq -r '.id')
echo -e "${GREEN}✓ Ticket 2: $TICKET2_ID${NC}"

TICKET3=$(curl -s -X POST "$BASE_URL/tickets/" \
  -H "Content-Type: application/json" \
  -d "{\"company_id\":\"$COMPANY_ID\",\"provider_name\":\"PAPELERIA CENTRAL\",\"provider_tax_id\":\"PAP010101XXX\",\"total_amount\":\"350.00\",\"tax_amount\":\"56.00\",\"expense_date\":\"2025-01-17\",\"category\":\"OFICINA\"}")

TICKET3_ID=$(echo "$TICKET3" | jq -r '.id')
echo -e "${GREEN}✓ Ticket 3: $TICKET3_ID${NC}"

# 3. Crear movimientos bancarios
echo -e "\n${YELLOW}4. Crear Movimientos Bancarios...${NC}"

BANK1=$(curl -s -X POST "$BASE_URL/bank-transactions/" \
  -H "Content-Type: application/json" \
  -d "{\"company_id\":\"$COMPANY_ID\",\"transaction_date\":\"2025-01-15\",\"amount\":\"-500.00\",\"description\":\"PAGO GAS PROVEEDOR SA\",\"reference\":\"REF001\"}")

BANK1_ID=$(echo "$BANK1" | jq -r '.id')
echo -e "${GREEN}✓ Banco 1: $BANK1_ID${NC}"

BANK2=$(curl -s -X POST "$BASE_URL/bank-transactions/" \
  -H "Content-Type: application/json" \
  -d "{\"company_id\":\"$COMPANY_ID\",\"transaction_date\":\"2025-01-16\",\"amount\":\"-1250.00\",\"description\":\"PAGO REFRESCOS MEXICO\",\"reference\":\"REF002\"}")

BANK2_ID=$(echo "$BANK2" | jq -r '.id')
echo -e "${GREEN}✓ Banco 2: $BANK2_ID${NC}"

BANK3=$(curl -s -X POST "$BASE_URL/bank-transactions/" \
  -H "Content-Type: application/json" \
  -d "{\"company_id\":\"$COMPANY_ID\",\"transaction_date\":\"2025-01-18\",\"amount\":\"-350.00\",\"description\":\"PAGO PAPELERIA CENTRAL\",\"reference\":\"REF003\"}")

BANK3_ID=$(echo "$BANK3" | jq -r '.id')
echo -e "${GREEN}✓ Banco 3: $BANK3_ID${NC}"

# 4. Ejecutar Conciliación
echo -e "\n${YELLOW}5. Ejecutar Conciliación Automática...${NC}"

RECON_RESPONSE=$(curl -s -X POST "$BASE_URL/reconciliations/run" \
  -H "Content-Type: application/json" \
  -d "{\"company_id\":\"$COMPANY_ID\",\"amount_tolerance\":\"0.01\",\"date_tolerance_days\":3}")

echo "$RECON_RESPONSE" | jq .

PERFECT=$(echo "$RECON_RESPONSE" | jq -r '.perfect_matches')
MANUAL=$(echo "$RECON_RESPONSE" | jq -r '.manual_review')
DISC=$(echo "$RECON_RESPONSE" | jq -r '.discrepancies')
UNMATCHED_T=$(echo "$RECON_RESPONSE" | jq -r '.unmatched_tickets')
UNMATCHED_B=$(echo "$RECON_RESPONSE" | jq -r '.unmatched_bank_transactions')

echo -e "\n${YELLOW}Resumen de Conciliación:${NC}"
echo -e "  Perfectos: ${GREEN}$PERFECT${NC}"
echo -e "  Manual: ${YELLOW}$MANUAL${NC}"
echo -e "  Discrepancias: ${RED}$DISC${NC}"
echo -e "  Tickets sin match: $UNMATCHED_T"
echo -e "  Bancos sin match: $UNMATCHED_B"

# 5. Listar conciliaciones creadas
echo -e "\n${YELLOW}6. Listar Conciliaciones...${NC}"
curl -s "$BASE_URL/reconciliations/?company_id=$COMPANY_ID" | jq .

# 6. Exportar Excel
echo -e "\n${YELLOW}7. Exportar Excel Estándar...${NC}"
curl -s "$BASE_URL/reconciliations/export/excel?company_id=$COMPANY_ID" \
  -o /tmp/conciliacion.xlsx
echo -e "${GREEN}✓ Guardado en /tmp/conciliacion.xlsx${NC}"

# 7. Exportar CONTPAQI
echo -e "\n${YELLOW}8. Exportar CONTPAQI...${NC}"
curl -s "$BASE_URL/reconciliations/export/contpaqi?company_id=$COMPANY_ID" \
  -o /tmp/contpaqi_polizas.xlsx
echo -e "${GREEN}✓ Guardado en /tmp/contpaqi_polizas.xlsx${NC}"

# 8. Exportar Genérico
echo -e "\n${YELLOW}9. Exportar Genérico (columnas personalizadas)...${NC}"
curl -s "$BASE_URL/reconciliations/export/generic?company_id=$COMPANY_ID&columns=Fecha,Proveedor,Total,Estatus%20Conciliacion" \
  -o /tmp/export_personalizado.xlsx
echo -e "${GREEN}✓ Guardado en /tmp/export_personalizado.xlsx${NC}"

echo -e "\n${YELLOW}===========================================${NC}"
echo -e "${GREEN}¡Todas las pruebas completadas exitosamente!${NC}"
echo -e "${YELLOW}===========================================${NC}"
echo ""
echo "Archivos generados:"
echo "  - /tmp/conciliacion.xlsx"
echo "  - /tmp/contpaqi_polizas.xlsx"
echo "  - /tmp/export_personalizado.xlsx"
echo ""
echo "Para verlos: open /tmp/conciliacion.xlsx (macOS) / xdg-open (Linux)"