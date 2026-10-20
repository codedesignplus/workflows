#!/usr/bin/env python3
"""Guardrail: sin codigo de retrocompatibilidad para datos guardados.

Cuando un campo deja de existir en una clase que Mongo guarda, se quita del codigo y los documentos se limpian en la
base con $unset (o el usuario limpia la base). No se deja un "sumidero" que lo siga leyendo para descartarlo, ni se
silencian los campos desconocidos: ese codigo no lo retira nadie y se acumula (regla 65 de rules/).

El script revisa src/ (sin bin/ ni obj/) y falla si encuentra, fuera de los comentarios:
- un sumidero de solo lectura: una propiedad `BsonValue? Legacy...`;
- `[BsonIgnoreExtraElements]` o `[BsonExtraElements]` en una clase;
- la convencion o el mapa que hacen lo mismo: `IgnoreExtraElementsConvention` o `SetIgnoreExtraElements(true)`.

    python legacy-fields-guardrails.py [raiz]
"""
import glob, os, re, sys

RAIZ = sys.argv[1] if len(sys.argv) > 1 else "."
ROJO, VERDE, RESET = "\033[0;31m", "\033[0;32m", "\033[0m"

PATRONES = [
    (re.compile(r"\bBsonValue\s*\??\s+Legacy\w*"), "sumidero de solo lectura de un campo retirado"),
    (re.compile(r"\[\s*(?:MongoDB\.Bson\.Serialization\.Attributes\.)?BsonIgnoreExtraElements\b"), "[BsonIgnoreExtraElements]"),
    (re.compile(r"\[\s*(?:MongoDB\.Bson\.Serialization\.Attributes\.)?BsonExtraElements\b"), "[BsonExtraElements]"),
    (re.compile(r"\bIgnoreExtraElementsConvention\b"), "IgnoreExtraElementsConvention"),
    (re.compile(r"\bSetIgnoreExtraElements\s*\(\s*true\s*\)"), "SetIgnoreExtraElements(true)"),
]


def sin_comentarios(txt):
    """Cambia los comentarios por espacios y deja intactos los textos y los saltos de linea."""
    salida, i, n = [], 0, len(txt)
    while i < n:
        c, s = txt[i], txt[i:i + 2]
        if s == "//":
            fin = txt.find("\n", i)
            fin = n if fin < 0 else fin
            salida.append(" " * (fin - i))
            i = fin
        elif s == "/*":
            fin = txt.find("*/", i + 2)
            fin = n if fin < 0 else fin + 2
            salida.append(re.sub(r"[^\n]", " ", txt[i:fin]))
            i = fin
        elif c == '"':
            fin = i + 1
            while fin < n and txt[fin] not in '"\n':
                fin += 2 if txt[fin] == "\\" else 1
            salida.append(txt[i:fin + 1])
            i = fin + 1
        else:
            salida.append(c)
            i += 1
    return "".join(salida)


hallazgos = []
for path in sorted(glob.glob(f"{RAIZ}/**/src/**/*.cs", recursive=True)):
    p = path.replace("\\", "/")
    if "/obj/" in p or "/bin/" in p:
        continue
    original = open(path, encoding="utf-8-sig", errors="ignore").read()
    codigo = sin_comentarios(original)
    lineas = original.split("\n")
    relativa = os.path.relpath(path, RAIZ).replace("\\", "/")
    for patron, motivo in PATRONES:
        for m in patron.finditer(codigo):
            numero = codigo.count("\n", 0, m.start()) + 1
            hallazgos.append(f"{relativa}:{numero}: {motivo}: {lineas[numero - 1].strip()}")

if hallazgos:
    for hallazgo in hallazgos:
        print(f"{ROJO}  FALLA {hallazgo}{RESET}")
    print(f"\n{ROJO}Un campo que se retira de una clase persistida se quita del codigo y se limpia en la base con $unset"
          f" (paso del plan, al publicar); nada de sumideros Legacy ni de ignorar elementos extra: regla 65 de rules/.{RESET}")
    sys.exit(1)

print(f"{VERDE}  OK  Campos retirados: sin sumideros ni elementos extra ignorados{RESET}")
