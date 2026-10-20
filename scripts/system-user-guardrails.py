#!/usr/bin/env python3
"""Guardrail: lo que hace el sistema se firma con SystemUser de Kappali.Net.Common.

Cada micro de Kappali firmaba lo que hace solo con un usuario distinto: 10000000-...-0001, 00000000-...-0001 o
Guid.Empty, que ademas se confunde con un dato que falta (pendings/493, regla 64 de rules/). El autor de un proceso,
un consumidor, una siembra o un aviso automatico es SystemUser.Id (o SystemUser.AsItem si el campo guarda id y nombre).

El script solo aplica a los micros de Kappali (un .csproj Kappali.Net.Microservice.*) y revisa src/. Falla si encuentra:
- un id de sistema escrito a mano ("10000000-0000-0000-0000-000000000001" o "00000000-0000-0000-0000-000000000001");
- el nombre "Sistema" escrito a mano;
- Guid.Empty (o `x ?? Guid.Empty`) como autor: asignado a un campo o argumento con nombre de autor (CreatedBy,
  UpdatedBy, sentBy, closedBy, author, actor...) o pasado por posicion a un metodo del propio micro cuyo parametro en
  esa posicion se llama asi. Las llamadas a metodos de paquetes no se resuelven por posicion: ahi se usa el argumento
  con nombre, como `sentBy:`.

Las violaciones que habia al crear la regla estan en `system-user-guardrails.exceptions`, junto a este script, una por
linea como `ruta | contenido de la linea`. Se borran al migrar cada micro; una excepcion que ya no casa se avisa.

    python system-user-guardrails.py [raiz] [--list]

Con --list imprime los hallazgos en el formato del fichero de excepciones, sin filtrarlos.
"""
import glob, os, re, sys

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
RAIZ = ARGS[0] if ARGS else "."
LISTAR = "--list" in sys.argv
ROJO, AMARILLO, VERDE, RESET = "\033[0;31m", "\033[0;33m", "\033[0;32m", "\033[0m"
EXCEPCIONES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "system-user-guardrails.exceptions")

RE_ID_LITERAL = re.compile(r'"[01]0000000-0000-0000-0000-000000000001"')
RE_NOMBRE_LITERAL = re.compile(r'"Sistema"')
RE_AUTOR = re.compile(r"^(?!(?:order|group|sort|then|split|partition)By$)(?:\w*By|author\w*|actor\w*|sender\w*|systemUser\w*)$", re.I)
PALABRAS_CLAVE = {
    "if", "for", "foreach", "while", "switch", "catch", "using", "lock", "return", "await", "new", "throw", "else",
    "in", "is", "as", "yield", "case", "when", "nameof", "typeof", "sizeof", "default", "checked", "unchecked", "fixed",
    "and", "or", "not", "base", "this", "var", "select", "where", "from", "let", "on", "equals", "into", "orderby",
}
MODIFICADORES = {"this", "params", "ref", "out", "in", "scoped", "readonly"}


def nombre_del_micro(raiz):
    """Kappali.Net.Microservice.<Nombre> si la raiz es un micro de Kappali; None si no."""
    for path in glob.glob(f"{raiz}/**/Kappali.Net.Microservice.*.csproj", recursive=True):
        return ".".join(os.path.basename(path).split(".")[:4])
    return None


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
        elif c == '"' or s in ('@"', '$"') or txt[i:i + 3] in ('$@"', '@$"'):
            inicio = i
            literal = "@" in txt[i:i + 3].split('"')[0]
            i = txt.index('"', i) + 1
            while i < n:
                if not literal and txt[i] == "\\":
                    i += 2
                    continue
                if txt[i] == '"':
                    if literal and txt[i + 1:i + 2] == '"':
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            salida.append(txt[inicio:i])
        elif c == "'":
            fin = i + 1
            while fin < n and txt[fin] != "'":
                fin += 2 if txt[fin] == "\\" else 1
            salida.append(txt[i:fin + 1])
            i = fin + 1
        else:
            salida.append(c)
            i += 1
    return "".join(salida)


def partir_nivel_cero(texto, genericos=False):
    """Parte por comas que no estan dentro de parentesis, corchetes o llaves (ni de genericos, en una firma)."""
    abren, cierran = ("([{<", ")]}>") if genericos else ("([{", ")]}")
    partes, nivel, actual = [], 0, []
    for c in texto:
        if c in abren:
            nivel += 1
        elif c in cierran:
            nivel -= 1
        if c == "," and nivel == 0:
            partes.append("".join(actual))
            actual = []
        else:
            actual.append(c)
    if "".join(actual).strip():
        partes.append("".join(actual))
    return partes


def cierre(txt, abre):
    nivel = 0
    for j in range(abre, len(txt)):
        if txt[j] == "(":
            nivel += 1
        elif txt[j] == ")":
            nivel -= 1
            if nivel == 0:
                return j
    return -1


RE_LLAMADA = re.compile(r"\b(\w+)\s*(?:<[\w\s,<>?\[\].]*>)?\s*\(")
RE_TIPO = re.compile(r"\b(?:class|record|struct|interface)\s+(\w+)")

# Metodos de paquetes que firman con un autor y que los micros llaman por posicion. El script corre con el repo del
# micro y sin el SDK, asi que sus firmas van aqui: son las extensiones de IInboxGrpc e INotificationGrpc de
# CodeDesignPlus.Net.gRpc.Clients, sin el `this`. Si el SDK las cambia, se cambian aqui.
EXTERNAS = {
    "NotifyUsersAsync": [["userIds", "kind", "title", "body", "module", "aggregateId", "payload", "tenant", "sentBy", "occurredAt", "notificationId", "cancellationToken"]],
    "NotifyRolesAsync": [["roles", "kind", "title", "body", "module", "aggregateId", "payload", "tenant", "sentBy", "occurredAt", "notificationId", "cancellationToken"]],
    "NotifyTenantAsync": [["kind", "title", "body", "module", "aggregateId", "payload", "tenant", "sentBy", "occurredAt", "notificationId", "cancellationToken"]],
    "NotifyUserAsync": [["userId", "eventName", "payload", "tenant", "sentBy", "cancellationToken"]],
    "BroadcastToTenantAsync": [["eventName", "payload", "tenant", "sentBy", "cancellationToken"]],
    "NotifyGroupAsync": [["groupName", "eventName", "payload", "tenant", "sentBy", "cancellationToken"]],
}


def declaraciones(txt):
    """Nombre -> lista de (parametros, obligatorios, admite params, es extension) de lo que declara un archivo."""
    resultado = {}
    for m in RE_LLAMADA.finditer(txt):
        nombre = m.group(1)
        if nombre in PALABRAS_CLAVE:
            continue
        previo = txt[:m.start()].rstrip()
        if not previo or not (previo[-1].isalnum() or previo[-1] in "_>?]"):
            continue
        palabra_previa = re.search(r"(\w+)$", previo)
        if palabra_previa and palabra_previa.group(1) in PALABRAS_CLAVE:
            continue
        abre = m.end() - 1
        fin = cierre(txt, abre)
        if fin < 0:
            continue
        nombres, obligatorios, variadico, extension, valido = [], 0, False, False, True
        for parte in partir_nivel_cero(txt[abre + 1:fin], genericos=True):
            parte = re.sub(r"\[[^\]]*\]", " ", parte).strip()
            tipo_y_nombre, _, por_defecto = parte.partition("=")
            fichas = [f for f in re.split(r"\s+", tipo_y_nombre.strip()) if f]
            if fichas and fichas[0] == "params":
                variadico = True
            if fichas and fichas[0] == "this" and not nombres:
                extension = True
            fichas = [f for f in fichas if f not in MODIFICADORES]
            if len(fichas) < 2 or not re.fullmatch(r"@?\w+", fichas[-1]):
                valido = False
                break
            nombres.append(fichas[-1].lstrip("@"))
            if not por_defecto:
                obligatorios += 1
        if valido:
            resultado.setdefault(nombre, []).append((nombres, obligatorios, variadico, extension))
    return resultado


def autor_por_posicion(firmas, indice, cantidad, como_miembro):
    candidatas = []
    for nombres, obligatorios, variadico, extension in firmas:
        if extension and como_miembro:
            nombres, obligatorios = nombres[1:], obligatorios - 1
        if obligatorios <= cantidad and (cantidad <= len(nombres) or variadico) and indice < len(nombres):
            candidatas.append(nombres)
    return bool(candidatas) and all(RE_AUTOR.match(nombres[indice]) for nombres in candidatas)


class Micro:
    """Las firmas del micro por archivo y por tipo, para resolver una llamada mas o menos como el compilador."""

    def __init__(self, codigos):
        self.por_archivo = {ruta: declaraciones(txt) for ruta, txt in codigos.items()}
        self.por_tipo = {}
        for ruta, txt in codigos.items():
            for tipo in RE_TIPO.findall(txt):
                self.por_tipo.setdefault(tipo, set()).add(ruta)
        self.globales = {}
        for firmas in self.por_archivo.values():
            for nombre, lista in firmas.items():
                self.globales.setdefault(nombre, []).extend(lista)

    def firmas(self, ruta, calificador, nombre):
        # Tipo.Metodo(...): las del tipo. Metodo(...) o this.Metodo(...): las del mismo archivo. Si no, las de todo el
        # micro, y si el micro no la declara, las de los paquetes conocidos.
        if calificador in self.por_tipo:
            lista = [f for r in self.por_tipo[calificador] for f in self.por_archivo[r].get(nombre, [])]
            if lista:
                return lista
        if calificador in (None, "this") and nombre in self.por_archivo[ruta]:
            return self.por_archivo[ruta][nombre]
        if nombre in self.globales:
            return self.globales[nombre]
        return [(nombres, len(nombres) - 1, False, False) for nombres in EXTERNAS.get(nombre, [])]


def guid_empty_como_autor(txt, ruta, micro):
    """Devuelve (posicion, motivo) de cada Guid.Empty que firma como autor."""
    hallazgos = []
    for m in re.finditer(r"\bGuid\.Empty\b", txt):
        # El argumento o la asignacion que contiene Guid.Empty: desde el limite anterior hasta el siguiente.
        nivel, j = 0, m.start() - 1
        while j >= 0:
            c = txt[j]
            if c in ")]}":
                nivel += 1
            elif c in "([{":
                if nivel == 0:
                    break
                nivel -= 1
            elif c in ",;" and nivel == 0:
                break
            j -= 1
        if not re.match(r"\s*[,;)\]}]", txt[m.end():]):
            continue
        segmento = " ".join(txt[j + 1:m.end()].split())
        nombrado = re.fullmatch(r"(?:[\w.]+\.)?(\w+)\s*(?<![=!<>])[:=](?!=)\s*(?:[^=?:]+\?\?\s*)?Guid\.Empty", segmento)
        if nombrado:
            if RE_AUTOR.match(nombrado.group(1)):
                hallazgos.append((m.start(), f"Guid.Empty como autor en {nombrado.group(1)}"))
            continue
        if not re.fullmatch(r"(?:[^=?:,]+\?\?\s*)?Guid\.Empty", segmento):
            continue
        # Por posicion: se busca el parentesis que abre la llamada y se cuentan los argumentos anteriores.
        indice, nivel, k = 0, 0, j
        while k >= 0:
            c = txt[k]
            if c in ")]}":
                nivel += 1
            elif c in "([{":
                if nivel == 0:
                    break
                nivel -= 1
            elif c == "," and nivel == 0:
                indice += 1
            elif c == ";" and nivel == 0:
                break
            k -= 1
        if k < 0 or txt[k] != "(":
            continue
        llamada = re.search(r"(?:(\w+)\s*(\??\.)\s*)?(\w+)\s*(?:<[\w\s,<>?\[\].]*>)?\s*$", txt[:k])
        if not llamada or llamada.group(3) in PALABRAS_CLAVE:
            continue
        fin = cierre(txt, k)
        argumentos = partir_nivel_cero(txt[k + 1:fin]) if fin > 0 else []
        if any(re.match(r"\s*\w+\s*:(?!:)", a) for a in argumentos[:indice + 1]):
            continue
        calificador, como_miembro, nombre = llamada.group(1), bool(llamada.group(2)), llamada.group(3)
        if autor_por_posicion(micro.firmas(ruta, calificador, nombre), indice, len(argumentos), como_miembro):
            hallazgos.append((m.start(), f"Guid.Empty como autor de {nombre}(...), argumento {indice + 1}"))
    return hallazgos


def leer_excepciones():
    entradas = set()
    if not os.path.exists(EXCEPCIONES):
        return entradas
    for linea in open(EXCEPCIONES, encoding="utf-8-sig"):
        linea = linea.rstrip("\n")
        if not linea.strip() or linea.lstrip().startswith("#"):
            continue
        ruta, _, contenido = linea.partition(" | ")
        entradas.add((ruta.strip(), contenido.strip()))
    return entradas


def main():
    micro_nombre = nombre_del_micro(RAIZ)
    if not micro_nombre:
        print(f"{VERDE}  OK  Usuario sistema: no es un micro de Kappali{RESET}")
        return 0

    archivos = {}
    for path in glob.glob(f"{RAIZ}/**/src/**/*.cs", recursive=True):
        p = path.replace("\\", "/")
        if "/obj/" in p or "/bin/" in p:
            continue
        relativa = os.path.relpath(path, RAIZ).replace("\\", "/")
        original = open(path, encoding="utf-8-sig", errors="ignore").read()
        archivos[relativa] = (original, sin_comentarios(original))

    micro = Micro({ruta: codigo for ruta, (_, codigo) in archivos.items()})

    hallazgos = []
    for relativa, (original, codigo) in sorted(archivos.items()):
        lineas = original.split("\n")
        encontrados = [(m.start(), "id de sistema escrito a mano") for m in RE_ID_LITERAL.finditer(codigo)]
        encontrados += [(m.start(), "nombre \"Sistema\" escrito a mano") for m in RE_NOMBRE_LITERAL.finditer(codigo)]
        encontrados += guid_empty_como_autor(codigo, relativa, micro)
        for posicion, motivo in sorted(encontrados):
            numero = codigo.count("\n", 0, posicion) + 1
            hallazgos.append((relativa, numero, lineas[numero - 1].strip(), motivo))

    if LISTAR:
        for relativa, _, contenido, _ in hallazgos:
            print(f"{relativa} | {contenido}")
        return 0

    excepciones = leer_excepciones()
    usadas = {(r, c) for r, _, c, _ in hallazgos if (r, c) in excepciones}
    pendientes = [h for h in hallazgos if (h[0], h[2]) not in excepciones]

    # Una excepcion de este micro que ya no casa sobra: se avisa, pero no se falla, para que migrar el micro no rompa su
    # CI antes de vaciar el fichero.
    for r, c in sorted(e for e in excepciones if f"/{micro_nombre}." in f"/{e[0]}" and e not in usadas):
        print(f"{AMARILLO}  AVISO {r} | {c}: la excepcion ya no casa; borrela de system-user-guardrails.exceptions{RESET}")

    if pendientes:
        for relativa, numero, contenido, motivo in pendientes:
            print(f"{ROJO}  FALLA {relativa}:{numero}: {motivo}: {contenido}{RESET}")
        print(f"\n{ROJO}Lo que hace el sistema se firma con SystemUser de Kappali.Net.Common (SystemUser.Id o"
              f" SystemUser.AsItem); nada de Guid.Empty ni ids de sistema escritos a mano: regla 64 de rules/.{RESET}")
        return 1

    print(f"{VERDE}  OK  Usuario sistema: sin firmas propias ({len(usadas)} excepcion(es) declarada(s) por migrar){RESET}")
    return 0


sys.exit(main())
