#!/usr/bin/env python3
"""Guardrail: todo AsyncWorker con jobs recurrentes los registra en Hangfire.

El SDK registra en Hangfire las clases IRecurrentJob con [RecurringJobOptions] dentro de UseHangfireDashboard, no en
AddHangfire. Un worker que tiene jobs pero no monta el dashboard compila, arranca y pasa sus pruebas, y sus jobs nunca
corren: asi pasaron dias sin vencer consejos ni comites, sin recordar paquetes, sin cerrar votaciones y sin escalar PQRS
(pendings/271, regla 58 de rules/).

Para cada proyecto entrypoint con una clase marcada con [RecurringJobOptions], el script exige:
- Program.cs llama a AddHangfire<Program>(...) y a UseHangfireDashboard<Program>(...);
- appsettings.json no apaga Hangfire (Hangfire:Enable false) ni deja el servidor sin workers (Hangfire:WorkerCount 0).

    python hangfire-guardrails.py [raiz]
"""
import glob, json, os, re, sys

RAIZ = sys.argv[1] if len(sys.argv) > 1 else "."
ROJO, VERDE, RESET = "\033[0;31m", "\033[0;32m", "\033[0m"

RE_JOB = re.compile(r"\[\s*RecurringJobOptions\s*\(")

proyectos = {}
for path in glob.glob(f"{RAIZ}/**/src/**/*.cs", recursive=True):
    p = path.replace("\\", "/")
    if "/obj/" in p or "/bin/" in p:
        continue
    txt = open(path, encoding="utf-8-sig", errors="ignore").read()
    if not RE_JOB.search(txt):
        continue
    # El proyecto es la carpeta que tiene el .csproj mas cercano hacia arriba.
    carpeta = os.path.dirname(path)
    while carpeta and not glob.glob(os.path.join(carpeta, "*.csproj")):
        padre = os.path.dirname(carpeta)
        if padre == carpeta:
            break
        carpeta = padre
    proyectos.setdefault(carpeta, []).append(p)

hallazgos = []
for carpeta, jobs in sorted(proyectos.items()):
    nombre = carpeta.replace("\\", "/")
    program = os.path.join(carpeta, "Program.cs")
    if not os.path.exists(program):
        hallazgos.append(f"{nombre}: tiene jobs ({len(jobs)}) pero no tiene Program.cs; los jobs van en el AsyncWorker")
        continue

    txt = open(program, encoding="utf-8-sig", errors="ignore").read()
    if not re.search(r"\bAddHangfire\s*<\s*Program\s*>\s*\(", txt):
        hallazgos.append(f"{nombre}/Program.cs: tiene jobs pero no llama a AddHangfire<Program>")
    if not re.search(r"\bUseHangfireDashboard\s*<\s*Program\s*>\s*\(", txt):
        hallazgos.append(f"{nombre}/Program.cs: tiene jobs pero no llama a UseHangfireDashboard<Program>, que es donde se registran")

    settings = os.path.join(carpeta, "appsettings.json")
    if os.path.exists(settings):
        try:
            hangfire = json.load(open(settings, encoding="utf-8-sig")).get("Hangfire") or {}
        except json.JSONDecodeError as error:
            hallazgos.append(f"{nombre}/appsettings.json: no es JSON valido ({error})")
            continue
        if hangfire.get("Enable") is False:
            hallazgos.append(f"{nombre}/appsettings.json: Hangfire:Enable es false y tiene jobs")
        if hangfire.get("WorkerCount") == 0:
            hallazgos.append(f"{nombre}/appsettings.json: Hangfire:WorkerCount es 0 y tiene jobs; nadie los ejecuta")

if hallazgos:
    for h in hallazgos:
        print(f"{ROJO}  FALLA {h}{RESET}")
    print(f"\n{ROJO}Un worker con [RecurringJobOptions] llama a AddHangfire<Program> y a UseHangfireDashboard<Program>, y no"
          f" apaga Hangfire: regla 58 de rules/.{RESET}")
    sys.exit(1)

print(f"{VERDE}  OK  Los jobs recurrentes se registran en Hangfire ({len(proyectos)} proyecto(s) con jobs){RESET}")
