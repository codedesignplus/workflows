#!/usr/bin/env python3
"""Guardrail: cada gemelo tiene que apuntar a un topic que alguien publique de verdad, y leer lo que ese
alguien escribe.

1. EL TOPIC. El nombre es {business}.{appName}.{version}.{entity}.{event}, y `entity` sale del atributo.
Con la forma generica —EventKey<Agregado>— el compilador coge el agregado que este a mano, que en un
consumidor suele ser uno SUYO y no el del microservicio que publica.

El resultado es una suscripcion colgada de un topic al que nadie publica, y no se ve por ninguna parte: la
suscripcion existe y tiene procesador, porque es el propio consumidor quien crea el topic al suscribirse
(AutoProvisionEntities). En el portal todo parece correcto y el mensaje no llega nunca.

Asi estuvo la regla contable de las areas comunes: al configurar el cobro, facturacion creaba su producto y
contabilidad no creaba nada, con lo que la reserva se cobraba sin llegar al mayor.

2. LA CARGA. Newtonsoft casa cada parametro del constructor del gemelo, y cada propiedad suya, con un campo
del JSON por nombre (sin distinguir mayusculas). Si el publicador no tiene una propiedad con ese nombre, el
valor llega por defecto —Guid.Empty, null, 0— sin un error. Asi estuvo el rubro de imprevistos: ms-administration
publicaba `Name` y el AggregateId, ms-expenses esperaba `categoryId` y `categoryName`, y la copropiedad
quedo con la categoria de imprevistos en Guid.Empty (pendings/207). El topic coincidia: la seccion 1 no lo veia.

Se comprueba cada parametro del constructor y cada propiedad del gemelo contra las propiedades publicas del
evento publicado, incluidas las de sus clases base y las de DomainEvent.

Se corre desde la raiz del espacio de trabajo, que es donde se ven todos los micros. Con la raiz de un solo micro
no hay nada que comparar y el script no da veredicto (pendings/695).

    python workflows/scripts/event-twin-guardrails.py .
"""
import glob, json, os, re, sys

RAIZ = sys.argv[1] if len(sys.argv) > 1 else "."
ROJO, VERDE, AMARILLO, RESET = "\033[0;31m", "\033[0;32m", "\033[0;33m", "\033[0m"

RE_GEN = re.compile(r'\[EventKey<(\w+)>\(\s*(\d+)\s*,\s*"([^"]+)"(?:\s*,\s*"([^"]*)")?(?:\s*,\s*"([^"]*)")?\s*\)\]')
RE_STR = re.compile(r'\[EventKey\(\s*"([^"]+)"\s*,\s*(\d+)\s*,\s*"([^"]+)"(?:\s*,\s*"([^"]*)")?(?:\s*,\s*"([^"]*)")?\s*\)\]')
RE_DECL = re.compile(r'\b(?:public|internal)\s+(?:(?:sealed|abstract|partial)\s+)*(class|record)\s+(\w+)')
RE_PROP = re.compile(r'\bpublic\s+(?:(?:required|virtual|override|new|static)\s+)*[\w<>\[\],.?]+(?:\s*<[^>{}]*>)?\??\s+(\w+)\s*\{\s*(?:get|init)')

# Lo que pone DomainEvent en el JSON: lo trae todo evento, lo declare o no.
BASE_DOMAIN_EVENT = {"aggregateid", "eventid", "occurredat", "metadata", "eventtype"}

# Bases del SDK que no estan en ningun micro: lo que anaden al JSON por encima de DomainEvent. Sin esto, las fotos de
# proyeccion (pendings/380) quedaban «sin comparar»: su publicador hereda de SnapshotDomainEvent, que vive en el SDK.
BASES_DEL_SDK = {
    "SnapshotDomainEvent": {"tenant", "requestid", "requestedby"},
}


def opciones(micro):
    """AppName y Business del microservicio, de su appsettings."""
    for ep in glob.glob(f"{micro}/src/entrypoints/*/appsettings.json"):
        p = ep.replace("\\", "/")
        if "/obj/" in p or "/bin/" in p:
            continue
        try:
            core = json.load(open(ep, encoding="utf-8-sig")).get("Core", {})
        except Exception:
            continue
        if core.get("AppName"):
            return core["AppName"].lower(), core.get("Business", "").lower()
    return None


def cierre(txt, i, abre, cierra):
    """Indice del delimitador que cierra el que abre en i."""
    nivel = 0
    for j in range(i, len(txt)):
        if txt[j] == abre:
            nivel += 1
        elif txt[j] == cierra:
            nivel -= 1
            if nivel == 0:
                return j
    return len(txt) - 1


def nombres_de_parametros(lista):
    """Nombres de los parametros de un constructor primario."""
    partes, nivel, actual = [], 0, ""
    for ch in lista:
        if ch in "<([{":
            nivel += 1
        if ch in ">)]}":
            nivel -= 1
        if ch == "," and nivel == 0:
            partes.append(actual)
            actual = ""
        else:
            actual += ch
    if actual.strip():
        partes.append(actual)
    nombres = []
    for parte in partes:
        parte = re.sub(r"\[[^\]]*\]", "", parte).split("=")[0].strip()
        if parte:
            nombres.append(parte.split()[-1])
    return nombres


def clases(txt):
    """Cada clase o record del fichero: constructor primario, clase base y propiedades publicas."""
    salida = []
    for m in RE_DECL.finditer(txt):
        tipo, nombre, i = m.group(1), m.group(2), m.end()
        while i < len(txt) and txt[i].isspace():
            i += 1
        if txt[i:i + 1] == "<":
            i = cierre(txt, i, "<", ">") + 1
            while i < len(txt) and txt[i].isspace():
                i += 1
        ctor = []
        if txt[i:i + 1] == "(":
            fin = cierre(txt, i, "(", ")")
            ctor = nombres_de_parametros(txt[i + 1:fin])
            i = fin + 1
        base = re.match(r"\s*:\s*([\w.]+)", txt[i:])
        llave, punto = txt.find("{", i), txt.find(";", i)
        cuerpo = "" if llave == -1 or (punto != -1 and punto < llave) else txt[llave:cierre(txt, llave, "{", "}") + 1]
        props = {p.group(1) for p in RE_PROP.finditer(cuerpo)}
        if tipo == "record":
            props |= {p[0].upper() + p[1:] for p in ctor}
        salida.append(dict(nombre=nombre, inicio=m.start(), ctor=ctor, base=base.group(1).split(".")[-1] if base else None, props=props))
    return salida


def eventos(micro, app, bus):
    """Todos los [EventKey] del microservicio, con su topic resuelto y la clase que lo declara."""
    salida, indice = [], {}
    for path in glob.glob(f"{micro}/src/**/*.cs", recursive=True):
        p = path.replace("\\", "/")
        if "/obj/" in p or "/bin/" in p:
            continue
        txt = open(path, encoding="utf-8", errors="ignore").read()
        decl = clases(txt)
        for c in decl:
            indice.setdefault(c["nombre"], c)
        for regex, gen in ((RE_GEN, True), (RE_STR, False)):
            for m in regex.finditer(txt):
                ent, ver, ev = m.group(1), m.group(2), m.group(3)
                a, b = m.group(4), m.group(5)
                clase = next((c for c in decl if c["inicio"] > m.end()), None)
                salida.append(dict(
                    path=p, linea=txt[:m.start()].count("\n") + 1, generico=gen, entity=ent,
                    topic=f"{(b or bus)}.{(a or app)}.v{ver}.{ent}.{ev}".lower(),
                    propio="/src/domain/" in p, texto=m.group(0), clase=clase, indice=indice))
    return salida


def campos_publicados(clase, indice):
    """Lo que el publicador escribe en el JSON: sus propiedades y las de sus bases. None si una base no se encuentra."""
    campos, actual, vistos = set(), clase, set()
    while actual is not None and actual["nombre"] not in vistos:
        vistos.add(actual["nombre"])
        campos |= {p.lower() for p in actual["props"]}
        base = actual["base"]
        if base in (None, "DomainEvent"):
            return campos | BASE_DOMAIN_EVENT
        if base in BASES_DEL_SDK:
            return campos | BASES_DEL_SDK[base] | BASE_DOMAIN_EVENT
        actual = indice.get(base)
    return None


def campos_leidos(clase):
    """Lo que el gemelo espera encontrar: los parametros de su constructor y sus propiedades."""
    nombres = {p.lower() for p in clase["ctor"]} | {p.lower() for p in clase["props"]}
    return nombres - BASE_DOMAIN_EVENT


micros = {}
for d in sorted(glob.glob(f"{RAIZ}/*.Net.Microservice.*")):
    o = opciones(d)
    if o:
        micros[d] = o

todos = []
for micro, (app, bus) in micros.items():
    todos.extend(eventos(micro, app, bus))

# Un topic tiene publicador si el evento se declara en la capa de dominio de algun microservicio:
# los gemelos viven siempre en entrypoints.
publicados = {e["topic"]: e for e in todos if e["propio"]}

# Sin gemelos o sin publicadores no hay nada que comparar, y un veredicto sobre nada miente: corrido con la raiz de
# un solo micro daba 0 gemelos revisados y aun asi todas las excepciones de CAMPOS_CONOCIDOS por obsoletas
# (pendings/695). Una falsa alarma ensena a ignorar el guardarrail. Sale con 2, que no es ni verde ni rojo.
if not any(not e["propio"] for e in todos) or not publicados:
    espacio = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    print(f"{AMARILLO}Nada que comparar en '{RAIZ}': {len(micros)} micro(s), "
          f"{sum(1 for e in todos if not e['propio'])} gemelo(s), {len(publicados)} evento(s) publicados.{RESET}")
    print("Este guardarrail se corre desde la raiz del espacio de trabajo, donde se ven todos los micros:\n")
    print(f"    cd {espacio}")
    print("    python workflows/scripts/event-twin-guardrails.py .")
    sys.exit(2)

# Deuda ya archivada en pendings/52: consumidores que esperan eventos que nadie llego a construir. Es un
# problema distinto —ahi el evento no existe, no es que el gemelo lo nombre mal— y esta pendiente de decidir
# si se borra el consumidor o se escribe el publicador. Se listan uno a uno a proposito: una lista explicita
# se revisa, un "ignorar lo de parking" se olvida.
#
# El 2026-09-22 la lista quedo VACIA y conviene que siga asi. De los siete que llego a tener: dos se borraron
# con la ficha 52 por ser el camino viejo del arriendo; tres se cablearon al agregado real con la ficha 138
# —el sorteo de parqueaderos—; y dos eran un FALSO POSITIVO del propio detector, que leia un evento publicado
# desde la carpeta de gemelos de ms-tenants como si fuera un gemelo sin publicador. Ese falso positivo casi
# cuesta borrar la compensacion del aprovisionamiento, que estaba viva. Se corrigio moviendo el evento a su
# capa de dominio.
CONOCIDOS = {
}

# Campos de un gemelo que el publicador no escribe y que se dejan asi a sabiendas: "topic::campo  # motivo".
# Sin motivo no vale. Un campo que se lee y nadie escribe llega siempre por defecto.
CAMPOS_CONOCIDOS = {
    # A proposito: desde pendings/325 el pago de un egreso se fecha el dia que se registra en Kappali, y PaymentDay cae al
    # dia del evento cuando falta. El campo se queda en el gemelo para un emisor que algun dia lo mande.
    "kappali.ms-expenses.v1.expenseaggregate.expensepaiddomainevent::paymentdate": "pendings/325",
    # Defectos reales que destapo esta comprobacion el 2026-10-13 (pendings/207). Son de otros modulos y estan por
    # archivar como planes propios: no se arreglan desde el de imprevistos. Cada uno se borra de aqui al arreglarse.
    "kappali.ms-parking.v1.parkingaggregate.parkingcreateddomainevent::type": "por archivar: el tipo de parqueadero no viaja",
    "kappali.ms-parking.v1.parkingaggregate.parkingupdateddomainevent::type": "por archivar: el tipo de parqueadero no viaja",
    "kappali.ms-residents.v1.residentaggregate.statusupdateddomainevent::isactive": "por archivar: el estado del residente no viaja a parqueaderos",
}

huerfanos = [e for e in todos if not e["propio"] and e["topic"] not in publicados]

rotos = [e for e in huerfanos if e["topic"] not in CONOCIDOS]
conocidos = [e for e in huerfanos if e["topic"] in CONOCIDOS]

print("Comprobando que cada gemelo apunte a un topic con publicador...\n")

for e in rotos:
    print(f"{ROJO}  GEMELO SIN PUBLICADOR{RESET}  {e['path'].split('/src/')[0]}")
    print(f"      {e['path'].split('/')[-1]}:{e['linea']}   {e['texto']}")
    print(f"{AMARILLO}      espera: {e['topic']}")
    if e["generico"]:
        print(f"      la forma generica cogio '{e['entity']}'. Nombre el agregado del publicador con la")
        print(f"      forma de texto: [EventKey(\"AgregadoDelPublicador\", ...)]{RESET}\n")
    else:
        print(f"      no hay ningun evento de dominio que resuelva a ese nombre.{RESET}\n")

if conocidos:
    print(f"{AMARILLO}deuda conocida, ya archivada en pendings/52 ({len(conocidos)} gemelos):{RESET}")
    for e in sorted(conocidos, key=lambda x: x["topic"]):
        print(f"    {e['topic']}")
    print()

print("Comprobando que cada gemelo lea campos que su publicador escribe...\n")

desparejados, sin_resolver, declarados_vistos = [], [], set()
for e in todos:
    if e["propio"] or e["topic"] not in publicados or e["clase"] is None:
        continue
    pub = publicados[e["topic"]]
    if pub["clase"] is None:
        sin_resolver.append(e)
        continue
    escritos = campos_publicados(pub["clase"], pub["indice"])
    if escritos is None:
        sin_resolver.append(e)
        continue
    sobrantes = campos_leidos(e["clase"]) - escritos
    declarados_vistos |= {f"{e['topic']}::{c}" for c in sobrantes}
    faltan = sorted(c for c in sobrantes if f"{e['topic']}::{c}" not in CAMPOS_CONOCIDOS)
    if faltan:
        desparejados.append((e, pub, faltan))

for e, pub, faltan in desparejados:
    print(f"{ROJO}  GEMELO CON CAMPOS QUE NADIE ESCRIBE{RESET}  {e['path'].split('/src/')[0]}")
    print(f"      {e['path'].split('/')[-1]}:{e['linea']}   {e['clase']['nombre']}")
    print(f"{AMARILLO}      no estan en el publicador ({pub['path'].split('/')[-1]}): {', '.join(faltan)}")
    print(f"      llegan siempre por defecto (Guid.Empty, null, 0). Llamelos como las propiedades del publicador.{RESET}\n")

# Una excepcion que ya no casa con nada se borra: o se arreglo el gemelo, o el publicador ya escribe el campo. Pero
# solo se puede decir si se escanearon los dos lados: el publicador del topic y algun gemelo que lo lea. Con una vista
# parcial, lo que no se ve se lista aparte, sin veredicto (pendings/695).
comparados = {e["topic"] for e in todos if not e["propio"] and e["clase"] is not None and e["topic"] in publicados}
revisables = [k for k in CAMPOS_CONOCIDOS if k.split("::")[0] in comparados]
sin_revisar = sorted(k for k in CAMPOS_CONOCIDOS if k not in revisables)
obsoletos = sorted(k for k in revisables if k not in declarados_vistos)
sin_motivo = sorted(k for k, v in CAMPOS_CONOCIDOS.items() if not str(v).strip())

for k in obsoletos:
    print(f"{ROJO}  EXCEPCION OBSOLETA{RESET}  {k}: el gemelo ya no lo lee o el publicador ya lo escribe. Borrela de CAMPOS_CONOCIDOS.")
for k in sin_motivo:
    print(f"{ROJO}  EXCEPCION SIN MOTIVO{RESET}  {k}")
if obsoletos or sin_motivo:
    print()

if sin_revisar:
    print(f"{AMARILLO}excepciones sin revisar porque en esta vista falta el gemelo o su publicador ({len(sin_revisar)}):{RESET}")
    for k in sin_revisar:
        print(f"    {k}")
    print()

if sin_resolver:
    print(f"{AMARILLO}gemelos sin comparar porque no se pudo leer la clase del publicador o su base ({len(sin_resolver)}):{RESET}")
    for e in sin_resolver:
        print(f"    {e['topic']}")
    print()

gemelos = sum(1 for e in todos if not e["propio"])
print(f"gemelos revisados: {gemelos}   eventos publicados: {len(publicados)}")

if rotos:
    print(f"\n{ROJO}{len(rotos)} gemelo(s) colgados de un topic al que nadie publica.{RESET}")
    print("\nNo se detecta desde el portal: el topic existe, la suscripcion esta activa y tiene procesador. Solo se")
    print("nota porque el mensaje no llega nunca, y para entonces el despliegue ya se dio por bueno.")

if desparejados:
    print(f"\n{ROJO}{len(desparejados)} gemelo(s) leen campos que su publicador no escribe.{RESET}")

if obsoletos or sin_motivo:
    print(f"\n{ROJO}{len(obsoletos) + len(sin_motivo)} excepcion(es) de CAMPOS_CONOCIDOS que no valen.{RESET}")

if rotos or desparejados or obsoletos or sin_motivo:
    sys.exit(1)

print(f"\n{VERDE}Todos los gemelos apuntan a un topic con publicador y leen lo que este escribe.{RESET}")
