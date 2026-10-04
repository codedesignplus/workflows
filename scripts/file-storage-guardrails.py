#!/usr/bin/env python3
"""Guardrail: fuera de ms-filestorage, el blob solo se lee.

Todo archivo vive en {contenedor}/{target}/{id}/{nombre} con su registro en ms-filestorage (regla 56 de rules/). El
registro es lo que permite darlo de baja con FilesReleased y que el job de limpieza borre el blob; sin el, el archivo se
queda para siempre. Un micro que escribe o borra directo con IFileStorage del SDK se salta el registro: asi quedaron los
documentos del vehiculo en vehicle-documents/{id}-{nombre} y el recibo de la licencia en licenses-pdf/{copropiedad}/
(pendings/260).

Lo que cada micro puede hacer:
- Guardar un archivo: el navegador lo sube por REST (POST /api/FileStorage/Upload); el backend lo manda por gRPC
  con IFileStorageGrpc.UploadAsync.
- Soltarlo: publicar FilesReleasedDomainEvent desde su agregado.
- Leerlo: IFileStorage.DownloadAsync o GetSignedUrlAsync, con la carpeta {target}/{id}.

Este script falla si un micro distinto de ms-filestorage llama a UploadAsync, DeleteAsync o DeleteTenantAsync sobre un
IFileStorage.

    python file-storage-guardrails.py [raiz]
"""
import glob, re, sys

RAIZ = sys.argv[1] if len(sys.argv) > 1 else "."
ROJO, VERDE, RESET = "\033[0;31m", "\033[0;32m", "\033[0m"

# El nombre con que el archivo recibe el IFileStorage: parametro del constructor primario, de un metodo o campo.
RE_INYECCION = re.compile(r"\bIFileStorage\s+(\w+)")
ESCRITURAS = ("UploadAsync", "DeleteAsync", "DeleteTenantAsync")

hallazgos = []
for path in glob.glob(f"{RAIZ}/**/src/**/*.cs", recursive=True):
    p = path.replace("\\", "/")
    if "/obj/" in p or "/bin/" in p or ".Microservice.FileStorage" in p:
        continue
    txt = open(path, encoding="utf-8", errors="ignore").read()
    for nombre in set(RE_INYECCION.findall(txt)):
        for m in re.finditer(rf"\b{re.escape(nombre)}\s*\.\s*({'|'.join(ESCRITURAS)})\s*\(", txt):
            linea = txt[:m.start()].count("\n") + 1
            hallazgos.append(f"{p}:{linea}: {nombre}.{m.group(1)} escribe en el blob sin pasar por ms-filestorage")

if hallazgos:
    for h in sorted(hallazgos):
        print(f"{ROJO}  FALLA {h}{RESET}")
    print(f"\n{ROJO}Un archivo se guarda por ms-filestorage (REST desde el navegador, IFileStorageGrpc desde el backend) y se"
          f" suelta con FilesReleasedDomainEvent: regla 56 de rules/.{RESET}")
    sys.exit(1)

print(f"{VERDE}  OK  Fuera de ms-filestorage el blob solo se lee{RESET}")
