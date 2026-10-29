# Runner de GitHub Actions para la Mac mini

Runner Linux en Docker para la organización `xiberty`. Se ejecuta en segundo plano,
con `restart: unless-stopped`, registro persistido y un Docker interno para los jobs
que usan `container`, `services` o construyen imágenes. La imagen comunitaria está
fijada a `myoung34/github-runner:2.338.2-ubuntu-noble`, disponible para ARM64 y AMD64.

Requisitos: Docker Desktop para macOS, o un motor Docker Linux accesible desde la
Mac, y Docker Compose v2 con `up --wait --wait-timeout`. El paquete no instala ni
arranca Docker Desktop o una VM por su cuenta.

## Primera puesta en marcha

1. Comprueba que Docker está funcionando. Si el runner antiguo de macOS sigue
   ejecutando `run.sh`, espera a que GitHub lo muestre como **Idle** y detén ese
   proceso con Ctrl+C.
2. Abre `xiberty > Settings > Actions > Runners > New runner > New self-hosted runner`.
   Selecciona **Linux** y **ARM64** para una Mac con chip de la serie M, o **x64**
   para Intel. Sólo necesitas el token temporal del comando `config.sh`; la imagen
   ya contiene el programa del runner. Genera uno nuevo si el anterior se compartió
   o ya caducó. No lo pegues en archivos ni en el chat.
3. En la terminal de la Mac mini ejecuta:

   ```bash
   cd /Users/vic/Projects/xiberty/github-actions
   ./runner.sh check
   ./runner.sh init
   ```

   `init` solicita el token sin mostrarlo, registra el runner en un contenedor
   temporal y elimina ese contenedor. Después arranca el contenedor permanente
   sin el token de alta en su entorno. No necesita un PAT permanente: reutiliza
   las credenciales del volumen de estado en los siguientes arranques.
4. En GitHub debe aparecer **mac-mini-docker**, con etiquetas `self-hosted`, `linux`,
   `mac-mini` y la arquitectura real. Comprueba que el grupo **Default** autorice
   los repositorios que quieras usar.

## Control diario

```bash
./runner.sh status
./runner.sh logs
```

Ctrl+C en `logs` sólo cierra el visor: el runner continúa funcionando.

- Arrancar después de haberlo detenido: `./runner.sh up`.
- Detener voluntariamente: `./runner.sh stop`.
- Reiniciar: `./runner.sh restart`.

Espera a que GitHub indique **Idle** antes de detenerlo o reiniciarlo para no cortar
un job. El healthcheck comprueba el Docker interno; el estado **Idle/Active** en
GitHub confirma la conexión del runner. Un healthcheck fallido se informa como
`unhealthy`; Docker no reinicia automáticamente sólo por ese estado. La política
de reinicio sí actúa cuando termina el proceso del contenedor.

Para sobrevivir a reinicios de la Mac, el motor Docker también tiene que arrancar
automáticamente. En Docker Desktop configura el inicio al entrar en sesión. Si
usas la VM Lima de la guía anterior, habilita su arranque automático. Mantén la Mac
sin reposo automático mientras deba atender jobs.

## Elegirlo desde los workflows

En cada job que deba ejecutarse aquí:

```yaml
runs-on: [self-hosted, linux, mac-mini]
```

Registrar el runner no modifica los workflows de ningún repositorio. En
`stilen-api`, hay que adaptar los cuatro jobs de `code_quality.yml` y
`build_deploy.yml`; este paquete no modifica esos archivos.

Si la Mac usa Apple Silicon, también hay que adaptar la descarga de `actionlint`
que actualmente está fijada a `linux_amd64`. La arquitectura de las imágenes de
producción debe seguir coincidiendo con la del servidor: si éste es AMD64,
configura QEMU/Buildx y `platforms: linux/amd64`.

## Prueba real antes de trasladar el CI

Copia `examples/runner-smoke.yml` a `.github/workflows/runner-smoke.yml` en un
repositorio privado autorizado. Si usa actionlint, añade la etiqueta `mac-mini` a
su configuración: este paquete incluye `actionlint.yaml` para copiarlo como
`.github/actionlint.yaml` o fusionarlo con el archivo existente. Envía los cambios
en una rama de prueba. Su push
dispara un workflow pequeño que comprueba:

- El Docker interno del runner.
- Un job dentro de `python:3.13-slim` y una acción JavaScript (`checkout`).
- Servicios Postgres 16 y Valkey 8, con DNS y conectividad desde el job.

La prueba no despliega la aplicación ni usa sus credenciales. Verifica ese
workflow en la Mac mini antes de dar por compatible el CI completo: ejecutar el
runner dentro de Docker tiene particularidades y limitaciones de soporte.

## Persistencia y mantenimiento

- `xiberty-gh-runner-state` guarda el registro del runner. Contiene credenciales:
  no lo publiques ni lo exportes al repositorio.
- Los otros volúmenes guardan el workspace y los datos del Docker interno.
- `stop`, `restart` y una recreación del contenedor conservan los volúmenes.
- El token de alta caduca en una hora; después del alta no se necesita para
  arrancar. Si eliminas el registro o el runner desde GitHub, habrá que registrarlo
  de nuevo con otro token.
- Para actualizar, elige una etiqueta publicada nueva en `compose.yaml`, espera
  a **Idle** y ejecuta `./runner.sh up`. El programa del runner mantiene habilitadas
  sus actualizaciones automáticas.

Para reinscribirlo **sólo si se borró o revocó el runner en GitHub**: detén el
paquete, retira su contenedor con `docker compose --env-file /dev/null down`,
elimina únicamente `xiberty-gh-runner-state` con `docker volume rm` y ejecuta de
nuevo `./runner.sh init`. Esto elimina las credenciales locales del registro,
conservando el workspace y el Docker interno.

El contenedor usa `privileged` para ejecutar su Docker interno. Dedícalo a
repositorios privados de confianza y limita qué repositorios tienen acceso al
grupo. No se publica ningún puerto hacia la Mac ni se monta el socket Docker del
anfitrión dentro del runner.

## Validación del paquete

```bash
bash -n runner.sh
shellcheck runner.sh
./runner.sh check
python3 -m unittest discover -s tests -v
actionlint -config-file actionlint.yaml examples/runner-smoke.yml
```

Las pruebas de los scripts usan un Docker simulado: no registran runners ni
arrancan contenedores privilegiados. El workflow de prueba es el que valida la
integración real después de proporcionar el token en la Mac mini.

## Fuentes

- [Imagen y opciones de configuración](https://github.com/myoung34/docker-github-actions-runner/tree/2.338.2).
- [Reutilización del registro en el entrypoint de la imagen](https://github.com/myoung34/docker-github-actions-runner/blob/2.338.2/entrypoint.sh).
- [Registro de runners de organización](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners).
- [Políticas de reinicio de Docker](https://docs.docker.com/engine/containers/start-containers-automatically/).
- [Particularidades del runner dentro de Docker](https://github.com/actions/runner/issues/406).
