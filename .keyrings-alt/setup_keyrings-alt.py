import os

# Localizar el directorio de configuración de keyring
# (compatible con versiones nuevas y antiguas de la librería).
try:
    import platformdirs
    ruta_config = str(platformdirs.user_config_path("python_keyring"))
except ImportError:
    from keyring.util.platform_ import config_root
    ruta_config = config_root()

os.makedirs(ruta_config, exist_ok=True)
archivo_config = os.path.join(ruta_config, "keyringrc.cfg")

# Configurar keyring para que use el backend de fichero en texto plano.
with open(archivo_config, "w") as f:
    f.write("[backend]\ndefault-keyring = keyrings.alt.file.PlaintextKeyring\n")

print(f"Configuración escrita correctamente en:\n{archivo_config}")
