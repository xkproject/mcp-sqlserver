import os
import keyring.util.platform_ as kp

# Directorio de configuración que la librería keyring espera utilizar.
ruta_teorica = kp.config_root()
print(f"Ruta de configuración esperada: {ruta_teorica}")

# Comprobar si ese directorio es accesible desde Python.
existe = os.path.exists(ruta_teorica)
print(f"Directorio accesible: {existe}\n")

if existe:
    print("Archivos encontrados (con su tamaño en disco):")
    archivos = os.listdir(ruta_teorica)
    for f in archivos:
        ruta_archivo = os.path.join(ruta_teorica, f)
        tamano = os.path.getsize(ruta_archivo)
        print(f"  - {f} ({tamano} bytes)")

    # Resolver la ruta real por si Windows está virtualizando el directorio.
    import pathlib
    ruta_real = pathlib.Path(ruta_teorica).resolve()
    if str(ruta_real) != ruta_teorica:
        print(f"\nWindows está redirigiendo la ruta. Ubicación real en disco:\n{ruta_real}")
else:
    print("El directorio no es accesible. Si las credenciales funcionan, esto no debería ocurrir.")
