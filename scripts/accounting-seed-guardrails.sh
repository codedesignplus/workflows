#!/usr/bin/env bash
#
# Guardrails del sembrado contable, que viven entre dos repositorios.
#
# Hay invariantes que ninguna prueba unitaria puede cubrir porque sus dos mitades estan en microservicios
# distintos. Son las que se rompen en silencio: nada falla al compilar, nada falla al arrancar, y el defecto
# aparece meses despues en un libro que no cuadra.
#
#   1. Toda categoria de gasto que siembra ms-administration tiene su cuenta en el seed de ms-accounting.
#      Sin el mapeo, la categoria nace sin cuenta y el egreso que se pague con ella va a la cuenta puente.
#
#      Y al reves: el seed no guarda nombres que ms-administration ya no siembra.
#
#   2. Todo producto con regla sembrada existe en KappaliProducts, el catalogo compartido del SDK, o en
#      AccountingProducts de ms-accounting, el de los productos que nadie factura (el cruce en efectivo, la
#      anulacion de un egreso de otro año). Un id escrito a mano en el JSON es una copia mas de un numero que
#      ya vivia en otro sitio.
#
#   3. Cada regla de retencion trae sus DOS cuentas, y cada una en su lado del balance. Lo que la
#      copropiedad retiene es un pasivo con la DIAN; lo que le retienen a ella es un anticipo a su favor.
#      Confundirlas deja el asiento cuadrado y el balance al reves, que es el error que no se ve.
#      Y lo que le retienen va a la auxiliar de su impuesto: renta a 135515, IVA a 135517 e ICA a 135518
#      (pendings/667).
#
#   4. Ninguna regla sembrada mueve una cuenta de grupo (pendings/660: 111005 es de grupo en las nuevas).
#
# Uso:  bash workflows/scripts/accounting-seed-guardrails.sh
set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Python es el de Windows aunque el shell sea Git Bash, y no entiende /d/... Se traduce cuando hace falta.
aruta() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else echo "$1"; fi
}

SEED="$RAIZ/Kappali.Net.Microservice.Accounting/src/entrypoints/Kappali.Net.Microservice.Accounting.AsyncWorker/Seeds/seed-co.json"
PROVISIONER="$RAIZ/Kappali.Net.Microservice.Administration/src/entrypoints/Kappali.Net.Microservice.Administration.AsyncWorker/Seeds/BudgetCategoryProvisioner.cs"
CATALOGO="$RAIZ/Kappali.Net.Sdk/packages/Kappali.Net.Common/src/Kappali.Net.Common/Products/KappaliProducts.cs"
CONTABLES="$RAIZ/Kappali.Net.Microservice.Accounting/src/domain/Kappali.Net.Microservice.Accounting.Domain/ServiceDomain/AccountingProducts.cs"

ROJO=$'\033[31m'; VERDE=$'\033[32m'; FIN=$'\033[0m'
FALLOS=0

for archivo in "$SEED" "$PROVISIONER" "$CATALOGO" "$CONTABLES"; do
  if [ ! -f "$archivo" ]; then
    echo "${ROJO}No se encuentra $archivo${FIN}"
    exit 2
  fi
done

SEED_W="$(aruta "$SEED")"
CATALOGO_W="$(aruta "$CATALOGO")"
CONTABLES_W="$(aruta "$CONTABLES")"

# Un guardrail que se cae y responde "todo bien" es peor que no tenerlo: pasaba justo aqui, porque una lista
# vacia por error de python es indistinguible de una lista vacia por estar todo correcto.
correr_python() {
  local salida
  # En UTF-8 y sin retorno de carro: en Windows python escribe en cp1252 y con CRLF, y asi ningun nombre con tilde
  # coincidia con los del provisionador (pendings/251).
  if ! salida=$(PYTHONIOENCODING=utf-8 python -c "$1" 2>&1 | tr -d '\r'); then
    echo "${ROJO}El guardrail no pudo ejecutarse:${FIN}"
    sed 's/^/   /' <<< "$salida"
    exit 2
  fi
  echo "$salida"
}

# ── 1. Cada categoria de gasto sembrada tiene cuenta ────────────────────────────
# Las categorias viven en una tupla de C# y las cuentas en un JSON: se comparan por nombre, que es
# precisamente lo fragil que este guardrail vigila.
CATEGORIAS=$(sed -n '/DefaultCategories =/,/];/p' "$PROVISIONER" \
  | grep -o '("[^"]*",[^)]*BudgetCategoryType\.Expense)' \
  | sed 's/^("//; s/".*//')

if [ -z "$CATEGORIAS" ]; then
  echo "${ROJO}No se pudo leer DefaultCategories de BudgetCategoryProvisioner.cs.${FIN}"
  exit 2
fi

MAPEADAS=$(correr_python "
import json
d = json.load(open(r'''$SEED_W''', encoding='utf-8'))
print('\n'.join(x['categoryName'] for x in d['expenseCategoryAccounts']))
")

SIN_CUENTA=()
while IFS= read -r categoria; do
  [ -z "$categoria" ] && continue
  grep -Fxq "$categoria" <<< "$MAPEADAS" || SIN_CUENTA+=("$categoria")
done <<< "$CATEGORIAS"

if [ ${#SIN_CUENTA[@]} -gt 0 ]; then
  echo "${ROJO}Categorias de gasto sembradas sin cuenta en seed-co.json:${FIN}"
  printf '   %s\n' "${SIN_CUENTA[@]}"
  echo "   Nacerian sin regla y su egreso iria a la cuenta puente."
  FALLOS=$((FALLOS+1))
else
  echo "${VERDE}Las $(wc -l <<< "$CATEGORIAS") categorias de gasto sembradas tienen cuenta.${FIN}"
fi

# ── 1b. Y el seed no guarda nombres que nadie siembra ─────────────────────────
# El mapeo se busca por nombre exacto (solo sin mayusculas; las tildes cuentan, pendings/251). Un nombre que
# ms-administration ya no siembra no le da cuenta a nadie, y deja creer que una categoria esta cubierta cuando su
# nombre vigente es otro: asi quedaron tres restos de antes de separar las categorias como el PUC.
SOBRANTES=()
while IFS= read -r mapeada; do
  [ -z "$mapeada" ] && continue
  grep -Fxq "$mapeada" <<< "$CATEGORIAS" || SOBRANTES+=("$mapeada")
done <<< "$MAPEADAS"

if [ ${#SOBRANTES[@]} -gt 0 ]; then
  echo "${ROJO}Nombres de seed-co.json que ms-administration no siembra:${FIN}"
  printf '   %s\n' "${SOBRANTES[@]}"
  echo "   Ninguna categoria los va a usar. Si son un nombre viejo, se borran; si son una categoria nueva, se siembran."
  FALLOS=$((FALLOS+1))
else
  echo "${VERDE}Cada nombre del seed-co.json es una categoria que ms-administration siembra.${FIN}"
fi

# ── 2. Todo producto con regla existe en el catalogo compartido ────────────────
HUERFANOS=$(correr_python "
import json, re
seed = json.load(open(r'''$SEED_W''', encoding='utf-8'))
catalogo = open(r'''$CATALOGO_W''', encoding='utf-8').read() + open(r'''$CONTABLES_W''', encoding='utf-8').read()
ids = {g.lower() for g in re.findall(r'Guid\.Parse\(\"([0-9a-fA-F-]+)\"\)', catalogo)}
print('\n'.join(r['productName'] for r in seed['accountingRules'] if r['productId'].lower() not in ids))
")

if [ -n "$HUERFANOS" ]; then
  echo "${ROJO}Reglas del seed cuyo producto no esta en KappaliProducts ni en AccountingProducts:${FIN}"
  sed 's/^/   /' <<< "$HUERFANOS"
  echo "   El id se escribio a mano en el JSON: es una copia mas de un numero que ya vive en el SDK o en ms-accounting."
  FALLOS=$((FALLOS+1))
else
  echo "${VERDE}Todos los productos con regla estan en el catalogo compartido.${FIN}"
fi

# ── 3. Cada retencion trae sus dos cuentas, cada una en su lado del balance ────
# Cuadrar no es estar bien: un asiento que lleva la retencion sufrida al pasivo de las practicadas suma
# igual y deja el balance al reves. La unica forma de verlo es mirar el tipo de la cuenta en el plan.
DESCUADRES=$(correr_python "
import json
d = json.load(open(r'''$SEED_W''', encoding='utf-8'))
plan = {a['code']: a for a in d['chartOfAccounts']['accounts']}
fallos = []
SUFRIDA_POR_TIPO = {'IncomeTax': '135515', 'Vat': '135517', 'Ica': '135518'}
# Lo que la copropiedad retiene va a la familia de su impuesto (pendings/699): renta a 2365, IVA a 2367 e ICA a 2368.
PRACTICADA_POR_TIPO = {'IncomeTax': '2365', 'Vat': '2367', 'Ica': '2368'}

for r in d['withholdings']:
    practicada = r.get('accountCode')
    sufrida = r.get('borneAccountCode')

    if not practicada or not r.get('accountName'):
        fallos.append(r['code'] + ': sin la cuenta de lo que la copropiedad retiene')

    if not sufrida or not r.get('borneAccountName'):
        fallos.append(r['code'] + ': sin la cuenta de lo que le retienen a la copropiedad')
        continue

    for codigo, tipo, familia, lado in ((practicada, 'Liability', PRACTICADA_POR_TIPO.get(r.get('kind', 'IncomeTax'), '2365'), 'practicada'), (sufrida, 'Asset', '1355', 'sufrida')):
        cuenta = plan.get(codigo)

        if cuenta is None:
            fallos.append(r['code'] + ': la cuenta ' + codigo + ' (' + lado + ') no esta en el plan')
        elif cuenta['type'] != tipo:
            fallos.append(r['code'] + ': ' + codigo + ' (' + lado + ') es ' + cuenta['type'] + ' y tiene que ser ' + tipo)
        elif not codigo.startswith(familia):
            fallos.append(r['code'] + ': ' + codigo + ' (' + lado + ') no cuelga de ' + familia)

    # Lo que le retienen a la copropiedad va a la auxiliar de su impuesto (pendings/667): renta a 135515, IVA a 135517 e ICA a
    # 135518. Juntas en 135515, la renta descontaba el ICA, que se cruza con el municipio y no con la DIAN.
    esperada = SUFRIDA_POR_TIPO.get(r.get('kind', 'IncomeTax'))
    if esperada and not sufrida.startswith(esperada):
        fallos.append(r['code'] + ': es de ' + r.get('kind', 'IncomeTax') + ' y lo sufrido va a ' + sufrida + ', no a ' + esperada)

print(chr(10).join(fallos))
")

if [ -n "$DESCUADRES" ]; then
  echo "${ROJO}Reglas de retencion mal apuntadas en seed-co.json:${FIN}"
  sed 's/^/   /' <<< "$DESCUADRES"
  echo "   Lo retenido acabaria en el lado contrario del balance, con el asiento cuadrado."
  FALLOS=$((FALLOS+1))
else
  echo "${VERDE}Las reglas de retencion apuntan a sus dos cuentas, cada una en su lado.${FIN}"
fi

# ── 4. Ninguna regla sembrada mueve una cuenta de grupo ──────────────────────────
# En Colombia el movimiento va a la auxiliar. Con la Wompi Cuenta (pendings/660), 111005 pasó a ser de grupo en las
# copropiedades nuevas: una regla que se quedara en 111005 asentaría en una cuenta que no recibe movimiento. Las cuentas
# marcadas newTenantsOnly cuentan como hijas: el plan de una copropiedad nueva las trae.
DE_GRUPO=$(correr_python "
import json
d = json.load(open(r'''$SEED_W''', encoding='utf-8'))
padres = {a.get('parentCode') for a in d['chartOfAccounts']['accounts'] if a.get('parentCode')}
fallos = []

for r in d['accountingRules']:
    for lado in ('debitAccountCode', 'creditAccountCode', 'recognitionAccountCode'):
        codigo = r.get(lado)
        if codigo and codigo in padres:
            fallos.append(r['productName'] + ': ' + lado + ' ' + codigo + ' tiene subcuentas')

for c in d.get('expenseCategoryAccounts', []):
    for lado in ('debitAccountCode', 'creditAccountCode'):
        codigo = c.get(lado)
        if codigo and codigo in padres:
            fallos.append(c['categoryName'] + ': ' + lado + ' ' + codigo + ' tiene subcuentas')

print(chr(10).join(fallos))
")

if [ -n "$DE_GRUPO" ]; then
  echo "${ROJO}Reglas sembradas contra una cuenta de grupo en seed-co.json:${FIN}"
  sed 's/^/   /' <<< "$DE_GRUPO"
  echo "   Una cuenta con subcuentas no recibe movimiento: la regla tiene que ir a una auxiliar."
  FALLOS=$((FALLOS+1))
else
  echo "${VERDE}Las reglas sembradas mueven solo cuentas auxiliares.${FIN}"
fi

exit $((FALLOS > 0 ? 1 : 0))
