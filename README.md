# Runner de GitHub Actions para la Mac mini

Runners Linux en Docker para la organización `xiberty`. Por defecto se levantan dos,
así que la Mac mini atiende dos jobs a la vez. Cada uno se ejecuta en segundo plano,
con `restart: unless-stopped`, registro persistido y su propio Docker interno para
los jobs que usan `container`, `services` o construyen imágenes. La imagen comunitaria está
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

   `init` solicita el token una sola vez y sin mostrarlo. Con él registra cada
   runner pendiente en un contenedor temporal, que elimina al terminar, y se salta
   los que ya tienen registro. Después arranca los contenedores permanentes sin el
   token de alta en su entorno. No necesita un PAT permanente: reutiliza las
   credenciales de los volúmenes de estado en los siguientes arranques.
4. En GitHub deben aparecer **mac-mini-docker** y **mac-mini-docker-2**, con las
   etiquetas `self-hosted`, `linux`, `mac-mini` y la arquitectura real. Comprueba
   que el grupo **Default** autorice los repositorios que quieras usar.

## Número de runners

`RUNNER_COUNT` indica cuántos runners gestiona cada comando (de 1 a 9; por defecto 2):

```bash
RUNNER_COUNT=3 ./runner.sh init   # registra sólo el tercero y arranca los tres
```

El runner 1 mantiene los nombres originales (`mac-mini-docker`, proyecto
`xiberty-github-runner`, volumen `xiberty-gh-runner-state`). Así, una instalación
anterior con un solo runner sigue usando su registro y `init` sólo da de alta el
segundo. El runner N usa `mac-mini-docker-N`, `xiberty-github-runner-N` y
`xiberty-gh-runner-N-state`. Todos comparten las mismas etiquetas: GitHub envía
cada job al primero que esté libre, sin cambiar los workflows.

Si usas un valor distinto de 2, pásalo en todos los comandos o exporta
`RUNNER_COUNT` en tu shell. Para reducir el número, detén primero los que sobran
con el valor antiguo: `RUNNER_COUNT=3 ./runner.sh stop`, seguido de
`RUNNER_COUNT=2 ./runner.sh up`.

### Pasar de un runner a dos

Si la Mac mini ya tiene el runner original funcionando:

1. Espera a que **mac-mini-docker** figure **Idle** en GitHub.
2. Actualiza el paquete (`git pull`) y genera un token nuevo en
   `xiberty > Settings > Actions > Runners > New self-hosted runner`.
3. Ejecuta `./runner.sh init`. Reutiliza el registro de `mac-mini-docker`, pide el
   token sólo para `mac-mini-docker-2` y arranca ambos. El contenedor del runner 1
   se recrea para montar `docker/daemon.json`, conservando sus volúmenes.
4. Comprueba con `./runner.sh status` que los dos estén `healthy` y, en GitHub,
   que figuren **Idle**.

### Cuántos runners caben

Estimación para una Mac mini de 16 GB:

| Consumo | Aproximado |
|---|---|
| macOS y aplicaciones abiertas | 3–4 GB |
| Memoria para la VM de Docker Desktop (Settings > Resources) | 10–12 GB |
| Un runner en reposo (runner + Docker interno) | ~200–300 MB |
| Un runner con un job con Postgres, Valkey o `docker build` | 1,5–3 GB en su pico |

Dos runners dejan margen; tres son el máximo razonable si coinciden jobs pesados.
Con más, la VM empieza a usar swap y todo va más lento que con menos runners. La
CPU también limita: una M-series de 8–10 núcleos se satura con 3–4 builds a la
vez. Revisa el consumo real con `docker stats` mientras corren jobs antes de
subir `RUNNER_COUNT`.

Ten en cuenta que:

- **Varios runners sólo ayudan si hay jobs que puedan correr a la vez.** Un
  workflow encadenado con `needs:`, o un grupo `concurrency:` que serializa
  ejecuciones, tarda lo mismo. La ganancia está en jobs independientes del mismo
  workflow y en pushes simultáneos de distintos repositorios.
- **Cada runner descarga sus propias imágenes**, así que el disco crece con cada
  runner. Consulta [Disco y caché](#disco-y-caché).

## Control diario

```bash
./runner.sh status
./runner.sh logs
```

`logs` muestra los de todos los runners, con el nombre de cada uno al inicio de
la línea. Ctrl+C sólo cierra el visor: los runners continúan funcionando.

- Arrancar después de haberlos detenido: `./runner.sh up`.
- Detener voluntariamente: `./runner.sh stop`.
- Reiniciar: `./runner.sh restart`.

Espera a que GitHub indique **Idle** antes de detenerlos o reiniciarlos para no
cortar un job. El healthcheck comprueba el Docker interno; el estado **Idle/Active** en
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

- `xiberty-gh-runner-state` y `xiberty-gh-runner-N-state` guardan el registro de
  cada runner. Contienen credenciales: no los publiques ni los exportes al
  repositorio.
- Los otros volúmenes, uno por runner, guardan el workspace y los datos del Docker
  interno.
- `stop`, `restart` y una recreación del contenedor conservan los volúmenes.
- El token de alta caduca en una hora; después del alta no se necesita para
  arrancar. Si eliminas el registro o el runner desde GitHub, habrá que registrarlo
  de nuevo con otro token.
- Para actualizar, elige una etiqueta publicada nueva en `compose.yaml`, espera
  a **Idle** y ejecuta `./runner.sh up`. El programa del runner mantiene habilitadas
  sus actualizaciones automáticas.

Para reinscribir un runner **sólo si se borró o revocó en GitHub**: detén los
runners y retira el contenedor afectado. Para el runner 2, por ejemplo:
`docker compose -p xiberty-github-runner-2 --env-file /dev/null down`. Elimina
únicamente su volumen de estado con `docker volume rm xiberty-gh-runner-2-state`
y ejecuta de nuevo `./runner.sh init`. Esto borra las credenciales locales de ese
registro y conserva el workspace y el Docker interno.

## Disco y caché

Cada runner tiene su propio Docker interno en el volumen `runner-docker`. Las
imágenes de `container:`/`services:` y la caché de `docker build` se guardan ahí y
no se comparten entre runners. Todo eso ocupa el disco virtual de Docker Desktop.
Hay tres controles:

1. **Caché de build, automática.** `docker/daemon.json` se monta en cada Docker
   interno y activa el recolector de BuildKit. La caché de un runner no pasa de
   10 GB, y el recolector actúa también si quedan menos de 15 GB libres en el
   disco, conservando siempre unos 2 GB. Para cambiar esos valores, edita el
   archivo y ejecuta `./runner.sh restart`.
2. **Imágenes, con `./runner.sh prune`.** El Docker interno no borra imágenes por
   su cuenta. `prune` limpia cada runner que esté Idle: borra contenedores
   detenidos, todas las imágenes sin uso, las redes y los volúmenes anónimos que
   dejan servicios como Postgres, y la caché de build que lleve más de 7 días sin
   usarse. Los runners que están ejecutando un job o detenidos se omiten. Tras la
   limpieza, el siguiente job vuelve a descargar las imágenes que necesite.

   Para que se ejecute cada día a las 4:00, copia
   `examples/com.xiberty.github-runner-prune.plist` a `~/Library/LaunchAgents/`
   (ajusta la ruta si el paquete está en otro sitio) y ejecuta:

   ```bash
   launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.xiberty.github-runner-prune.plist
   ```

   El resultado queda en `/tmp/xiberty-github-runner-prune.log`.
3. **Límite total en Docker Desktop.** En Settings > Resources > Disk usage limit,
   fija el tamaño máximo del disco virtual para que nunca ocupe todo el SSD de la
   Mac.

Para consultar el espacio usado: `docker system df -v` en la Mac muestra el tamaño
de cada volumen. Para el detalle de un runner, ejecuta dentro de él
`docker compose -p xiberty-github-runner-2 --env-file /dev/null exec runner docker system df`.

Cada contenedor usa `privileged` para ejecutar su Docker interno. Dedícalos a
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
plutil -lint examples/com.xiberty.github-runner-prune.plist
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
