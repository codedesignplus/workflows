#!/usr/bin/env python3
"""Guardrail: dos avisos que nacen del mismo evento no comparten id.

La bandeja (ms-notification) usa el id del aviso como clave de idempotencia: si le llega un aviso con un id que ya tiene,
lo descarta. Quien avisa desde un evento deriva el id del EventId con NotificationIds.For(eventId, tipo), para que una
reentrega del bus llegue con el mismo id. Pero si dos avisos del mismo evento llevan el mismo id, el segundo se pierde:
el 25-oct-2026 la administracion no recibia «Impugnacion radicada» ni «Asamblea extraordinaria por convocar» porque los
dos avisos de ms-assemblies salian con el EventId tal cual (pendings/623).

El script solo aplica a los micros de Kappali (un .csproj Kappali.Net.Microservice.*) y revisa src/. Mira el id de cada
NotifyUsersAsync, NotifyRolesAsync y NotifyTenantAsync (por nombre, `notificationId:`, o por posicion) y falla si:

1. el id es el del evento tal cual (`data.EventId`, `eventId`): lo comparte con cualquier otro aviso del mismo evento, en
   este micro o en otro que lo consuma. Se pasa NotificationIds.For(eventId, "<tipo>/<destinatario>");
2. dos avisos derivan el id con la misma expresion para el mismo evento: en el mismo metodo, o en dos metodos que reciben
   el mismo tipo de evento (dos consumidores del mismo evento). El evento es el parametro `<Algo>DomainEvent` del metodo; si
   no tiene, solo se comparan los avisos del mismo metodo. Dos avisos en ramas excluyentes del mismo metodo no chocan: el
   primero tiene que cerrar su rama (`return inbox.Notify...(...)` o `await inbox.Notify...(...); return;`).

Lo que no ve: un id calculado en una variable o en un metodo aparte, y un aviso dentro de un bucle. Ahi lo cubren las
pruebas del consumidor.

    python notification-id-guardrails.py [raiz]
"""
import glob, os, re, sys

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
RAIZ = ARGS[0] if ARGS else "."
ROJO, VERDE, RESET = "\033[0;31m", "\033[0;32m", "\033[0m"

# Posicion del id del aviso en las extensiones de IInboxGrpc de CodeDesignPlus.Net.gRpc.Clients, sin el `this`. El script
# corre con el repo del micro y sin el SDK, asi que van aqui: si el SDK las cambia, se cambian aqui.
POSICION_DEL_ID = {"NotifyUsersAsync": 10, "NotifyRolesAsync": 10, "NotifyTenantAsync": 9}

RE_LLAMADA = re.compile(r"\.\s*(NotifyUsersAsync|NotifyRolesAsync|NotifyTenantAsync)\s*\(")
RE_ID_DEL_EVENTO = re.compile(r"^(?:[\w]+\s*\.\s*)*(?:EventId|eventId)$")
RE_USA_EL_EVENTO = re.compile(r"\b(?:EventId|eventId)\b")
RE_METODO = re.compile(
    r"(?:public|private|internal|protected)\s+(?:(?:static|async|override|virtual|sealed|new)\s+)*"
    r"[\w<>\[\],.?\s]+?\s+(\w+)\s*(?:<[^>(]*>)?\s*\(")
RE_EVENTO = re.compile(r"\b(\w+DomainEvent)\s+\w+")


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


def cierre(txt, abre):
    """La posicion del parentesis que cierra el que abre en `abre`, o -1."""
    nivel = 0
    for j in range(abre, len(txt)):
        if txt[j] == "(":
            nivel += 1
        elif txt[j] == ")":
            nivel -= 1
            if nivel == 0:
                return j
    return -1


def partir_nivel_cero(texto):
    """Parte por comas que no estan dentro de parentesis, corchetes o llaves."""
    partes, nivel, actual = [], 0, []
    for c in texto:
        if c in "([{":
            nivel += 1
        elif c in ")]}":
            nivel -= 1
        if c == "," and nivel == 0:
            partes.append("".join(actual))
            actual = []
        else:
            actual.append(c)
    if "".join(actual).strip():
        partes.append("".join(actual))
    return partes


def id_del_aviso(metodo, argumentos):
    """La expresion del id del aviso, o None si la llamada no lo pasa (la bandeja genera uno nuevo)."""
    for argumento in argumentos:
        nombrado = re.match(r"\s*notificationId\s*:(?!:)\s*(.*)$", argumento, re.S)
        if nombrado:
            return nombrado.group(1).strip()
    posicion = POSICION_DEL_ID[metodo]
    if len(argumentos) > posicion and not re.match(r"\s*\w+\s*:(?!:)", argumentos[posicion]):
        return argumentos[posicion].strip()
    return None


def metodo_que_la_contiene(txt, posicion):
    """(inicio, nombre, tipo de evento o None) del ultimo metodo declarado antes de `posicion`."""
    ultimo = None
    for m in RE_METODO.finditer(txt, 0, posicion):
        ultimo = m
    if not ultimo:
        return (0, None, None)
    abre = ultimo.end() - 1
    fin = cierre(txt, abre)
    parametros = txt[abre + 1:fin] if fin > 0 else ""
    evento = RE_EVENTO.search(parametros)
    return (ultimo.start(), ultimo.group(1), evento.group(1) if evento else None)


def cierra_su_rama(txt, inicio, fin):
    """Si la llamada es lo ultimo de su rama: `return <llamada>` o `<llamada>; return;`."""
    antes = txt[:inicio]
    receptor = re.search(r"(?:await\s+)?[\w.?]+\s*$", antes)
    previo = antes[:receptor.start()] if receptor else antes
    if re.search(r"\breturn\s*$", previo):
        return True
    return bool(re.match(r"\s*\)?\s*;\s*return\s*;", txt[fin + 1:]))


def main():
    if not nombre_del_micro(RAIZ):
        print(f"{VERDE}  OK  Ids de avisos: no es un micro de Kappali{RESET}")
        return 0

    avisos = []
    for path in sorted(glob.glob(f"{RAIZ}/**/src/**/*.cs", recursive=True)):
        p = path.replace("\\", "/")
        if "/obj/" in p or "/bin/" in p:
            continue
        relativa = os.path.relpath(path, RAIZ).replace("\\", "/")
        codigo = sin_comentarios(open(path, encoding="utf-8-sig", errors="ignore").read())
        for m in RE_LLAMADA.finditer(codigo):
            abre = m.end() - 1
            fin = cierre(codigo, abre)
            if fin < 0:
                continue
            expresion = id_del_aviso(m.group(1), partir_nivel_cero(codigo[abre + 1:fin]))
            if expresion is None:
                continue
            inicio_metodo, nombre_metodo, evento = metodo_que_la_contiene(codigo, m.start())
            avisos.append({
                "ruta": relativa,
                "linea": codigo.count("\n", 0, m.start()) + 1,
                "id": " ".join(expresion.split()),
                "metodo": (relativa, inicio_metodo),
                "nombre_metodo": nombre_metodo,
                "evento": evento,
                "cierra": cierra_su_rama(codigo, m.start(), fin),
            })

    fallas = []
    for aviso in avisos:
        if RE_ID_DEL_EVENTO.match(aviso["id"]):
            fallas.append(f"{aviso['ruta']}:{aviso['linea']}: el id del aviso es el del evento tal cual ({aviso['id']});"
                          f" derivalo con NotificationIds.For(eventId, \"<tipo>/<destinatario>\")")

    vistos = {}
    for aviso in avisos:
        if not RE_USA_EL_EVENTO.search(aviso["id"]) or RE_ID_DEL_EVENTO.match(aviso["id"]):
            continue
        ambito = ("evento", aviso["evento"]) if aviso["evento"] else ("metodo", aviso["metodo"])
        for anterior in vistos.get((ambito, aviso["id"]), []):
            if anterior["metodo"] == aviso["metodo"] and anterior["cierra"]:
                continue
            donde = "el mismo metodo" if anterior["metodo"] == aviso["metodo"] else f"{anterior['ruta']}:{anterior['linea']}"
            fallas.append(f"{aviso['ruta']}:{aviso['linea']}: el mismo id que el aviso de {donde} para el mismo evento"
                          f" ({aviso['id']}); el segundo se descarta en la bandeja")
            break
        vistos.setdefault((ambito, aviso["id"]), []).append(aviso)

    if fallas:
        for falla in fallas:
            print(f"{ROJO}  FALLA {falla}{RESET}")
        print(f"\n{ROJO}Cada aviso que nace de un evento lleva su propio id, derivado del evento y de su destinatario o tipo:"
              f" NotificationIds.For(eventId, \"<tipo>/<destinatario>\"). pendings/623.{RESET}")
        return 1

    print(f"{VERDE}  OK  Ids de avisos: {len(avisos)} aviso(s) con id, ninguno compartido{RESET}")
    return 0


sys.exit(main())
