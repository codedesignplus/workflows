#!/usr/bin/env bash
# Guardrail de la regla 27: la lista de enums no crece sin justificarse.
#
# Compara los "public enum" del arbol contra rules/inventario-enums.md y falla si aparece
# alguno que no este inventariado. No comprueba que esten bien clasificados —eso es el
# pendiente 45, una revision por modulo—: comprueba que ninguno entre sin que alguien lo
# haya mirado.
#
#   bash workflows/scripts/enum-inventory.sh                 # desde la raiz del monorepo
#   bash .guardrails/scripts/enum-inventory.sh . ../rules    # dentro de un microservicio
#
# La solucion de cada enum se deduce del .sln mas cercano hacia arriba, no del primer
# segmento de la ruta: asi la misma fila del inventario sale igual se ejecute desde la raiz
# o desde el checkout de un solo micro, que es como corre en el CI de cada repo.

set -uo pipefail

raiz="${1:-.}"
raiz="${raiz%/}"
inventario="${2:-$raiz/rules}/inventario-enums.md"

if [ ! -f "$inventario" ]; then
    echo "ERROR: no existe $inventario" >&2
    exit 2
fi

# El nombre de la solucion dueña de un archivo: el directorio del .sln mas cercano hacia arriba.
solucion_de() {
    local dir anterior
    dir=$(dirname "$1")

    # La condicion de parada es que dirname deje de moverse, no que llegue a "." o a "/": ejecutando
    # desde dentro de un micro el .sln esta justo en "." y hay que mirarlo, no saltarselo.
    while [ -n "$dir" ]; do
        if compgen -G "$dir"/*.sln > /dev/null 2>&1; then
            # basename de "." no sirve; hace falta la ruta real para saber como se llama la carpeta.
            basename "$(cd "$dir" && pwd)"
            return
        fi

        anterior="$dir"
        dir=$(dirname "$dir")

        [ "$dir" = "$anterior" ] && break
    done

    # Sin .sln a la vista: el archivo no pertenece a ninguna solucion .NET.
    echo "sin-solucion"
}

declarados=$(
    grep -rl --include="*.cs" -E "^[[:space:]]*public enum " "$raiz" 2>/dev/null \
      | grep -v "/obj/" | grep -v "/bin/" \
      | while IFS= read -r archivo; do
            sol=$(solucion_de "$archivo")
            grep -hoE "^[[:space:]]*public enum [A-Za-z0-9_]+" "$archivo" \
              | sed -E "s|^[[:space:]]*public enum ([A-Za-z0-9_]+)|\1\|$sol|"
        done \
      | sort -u
)

inventariados=$(
    grep -E "^\| [A-Za-z0-9_]+ \| " "$inventario" \
      | awk -F'|' '{gsub(/^ +| +$/, "", $2); gsub(/^ +| +$/, "", $3); if ($2 != "Enum") print $2 "|" $3}' \
      | sort -u
)

faltantes=$(comm -23 <(echo "$declarados") <(echo "$inventariados"))

if [ -n "$faltantes" ]; then
    echo "REGLA 27 — enums sin inventariar:"
    echo ""
    echo "$faltantes" | sed 's/|/  en  /' | sed 's/^/  /'
    echo ""
    echo "No basta con anadir la fila."
    echo ""
    echo "Pase cada uno por las tres pruebas de rules/27-un-enum-se-justifica-o-es-un-catalogo.md, seccion 1:"
    echo "  1. Nada se bifurca sobre el                 -> es un catalogo, siembrelo"
    echo "  2. La lista no es nuestra                   -> es un catalogo, aunque hoy nos bifurquemos"
    echo "  3. Alguien de fuera podria anadir un valor  -> es un catalogo"
    echo ""
    echo "Si sobrevive a las tres, justifiquelo: nombre la bifurcacion concreta que habria que"
    echo "escribir para anadir un valor, y ponga esa linea en rules/inventario-enums.md."
    echo "Si no puede escribir esa linea, no es un enum."
    exit 1
fi

# Al reves: filas que ya no corresponden a nada. No falla —un enum borrado es buena noticia— pero
# solo tiene sentido avisar cuando se recorrio el monorepo entero; desde un micro, los enums de los
# otros 24 faltan por construccion.
if [ "$(echo "$declarados" | cut -d'|' -f2 | sort -u | grep -c .)" -gt 1 ]; then
    sobrantes=$(comm -13 <(echo "$declarados") <(echo "$inventariados"))

    if [ -n "$sobrantes" ]; then
        echo "AVISO — filas del inventario que ya no existen en el codigo (borrelas):"
        echo "$sobrantes" | sed 's/|/  en  /' | sed 's/^/  /'
        echo ""
    fi
fi

echo "OK — $(echo "$declarados" | grep -c .) enums, todos inventariados."
