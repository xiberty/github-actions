#!/usr/bin/env bash
set -Eeuo pipefail

package_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly package_dir
readonly compose_file="${package_dir}/compose.yaml"
readonly runner_count="${RUNNER_COUNT:-2}"
bootstrap_token="${RUNNER_TOKEN:-}"
unset RUNNER_TOKEN

on_error() {
  local runner_exit_status="$1" runner_error_line="$2"
  printf 'Error en línea %s (código %s).\n' "$runner_error_line" "$runner_exit_status" >&2
}
trap 'on_error "$?" "$LINENO"' ERR
trap 'unset RUNNER_TOKEN bootstrap_token' EXIT

usage() {
  cat <<'USAGE'
Uso: [RUNNER_COUNT=<n>] ./runner.sh <comando>

  init     Registrar los runners que falten con un token temporal y arrancarlos.
  up       Arrancar usando los registros guardados; no pide token.
  status   Mostrar estado y salud de cada contenedor.
  logs     Seguir los logs de todos; Ctrl+C sólo cierra el visor.
  stop     Detener los runners conservando sus datos.
  restart  Reiniciar los runners y esperar a su Docker interno.
  prune    Borrar imágenes y caché sin uso de los runners que estén Idle.
  check    Validar el archivo Compose sin arrancar ningún runner.

RUNNER_COUNT indica cuántos runners se gestionan (1-9, por defecto 2).
Antes de stop o restart, espera a que GitHub muestre los runners como Idle.
USAGE
}

die() {
  printf '%s\n' "$1" >&2
  exit "${2:-1}"
}

# El runner 1 conserva los nombres originales para reutilizar su registro.
select_runner() {
  local suffix=''
  if (( $1 > 1 )); then
    suffix="-$1"
  fi
  runner_project="xiberty-github-runner${suffix}"
  export RUNNER_NAME="mac-mini-docker${suffix}"
  export RUNNER_STATE_VOLUME="xiberty-gh-runner${suffix}-state"
}

compose() {
  docker compose --project-directory "$package_dir" --project-name "$runner_project" \
    --env-file /dev/null -f "$compose_file" "$@"
}

require_compose() {
  command -v docker >/dev/null 2>&1 || die 'Instala o inicia Docker Desktop, o usa Docker en tu VM Linux.'
  docker compose version >/dev/null 2>&1 || die 'Se necesita Docker Compose v2 con soporte para --wait.'
}

require_daemon() {
  local server_os
  if ! server_os="$(docker info --format '{{.OSType}}' 2>/dev/null)"; then
    die 'Docker no está disponible. Inicia Docker Desktop o la VM Linux.'
  fi
  [[ "$server_os" == 'linux' ]] || die 'Este paquete necesita un motor Docker Linux.'
}

ensure_image() {
  image_name="$(compose config --images)"
  [[ -n "$image_name" ]] || die 'No se pudo resolver la imagen del runner.'
  if ! docker image inspect "$image_name" >/dev/null 2>&1; then
    compose pull runner
  fi
}

is_registered() {
  local volume_names probe_status=0
  volume_names="$(docker volume ls --format '{{.Name}}')" || die 'No se pudo consultar los volúmenes de Docker.'
  if ! grep -Fx "$RUNNER_STATE_VOLUME" <<< "$volume_names" >/dev/null; then
    return 1
  fi

  # Sólo comprueba que existe el registro: no lee ni imprime las credenciales.
  docker run --rm --network none --read-only --cap-drop ALL \
    --security-opt no-new-privileges \
    --mount "type=volume,src=${RUNNER_STATE_VOLUME},dst=/state,readonly" \
    --entrypoint /bin/sh "$image_name" -c 'test -s /state/.runner' || probe_status=$?
  case "$probe_status" in
    0) return 0 ;;
    1) return 1 ;;
    *) die 'No se pudo comprobar el registro guardado; revisa Docker.' ;;
  esac
}

require_registrations() {
  local index
  for ((index = 1; index <= runner_count; index++)); do
    select_runner "$index"
    is_registered || die "No existe un registro guardado para ${RUNNER_NAME}. Ejecuta ./runner.sh init."
  done
}

start_runners() {
  local index
  for ((index = 1; index <= runner_count; index++)); do
    select_runner "$index"
    compose up -d --wait --wait-timeout 180 runner
  done
  printf '\nRunners arrancados. Comprueba que figuren Idle en GitHub.\n'
  show_status
}

show_status() {
  local index
  for ((index = 1; index <= runner_count; index++)); do
    select_runner "$index"
    printf '\n%s\n' "$RUNNER_NAME"
    compose ps runner
  done
}

read_token() {
  [[ -z "$bootstrap_token" ]] || return 0
  printf 'Genera un token nuevo en xiberty > Settings > Actions > Runners.\n'
  printf 'Sirve para todos los runners pendientes mientras no caduque.\n'
  if ! IFS= read -r -s -p 'Token temporal de registro: ' bootstrap_token </dev/tty; then
    die 'Ejecuta init desde una terminal, o proporciona RUNNER_TOKEN en su entorno.'
  fi
  printf '\n'
}

initialize_runners() {
  local index
  select_runner 1
  ensure_image
  for ((index = 1; index <= runner_count; index++)); do
    select_runner "$index"
    if is_registered; then
      printf '%s ya está registrado; se reutilizará sin pedir token.\n' "$RUNNER_NAME"
      continue
    fi

    read_token
    [[ -n "$bootstrap_token" && "$bootstrap_token" != *[[:space:]]* ]] \
      || die 'El token está vacío o contiene espacios/saltos de línea.'

    # El token sólo llega a este contenedor de alta, que se elimina al terminar.
    # El entrypoint de la imagen registra y guarda las credenciales antes de /bin/true.
    printf 'Registrando %s...\n' "$RUNNER_NAME"
    export RUNNER_TOKEN="$bootstrap_token"
    compose run --rm --no-deps -T -e START_DOCKER_SERVICE=false runner /bin/true
    unset RUNNER_TOKEN

    is_registered || die "El alta de ${RUNNER_NAME} no dejó un registro válido. Consulta el error anterior."
  done
  unset bootstrap_token
  start_runners
}

follow_logs() {
  local index
  for ((index = 1; index <= runner_count; index++)); do
    select_runner "$index"
    compose logs --tail 100 --follow --no-log-prefix runner 2>&1 \
      | awk -v prefix="[${RUNNER_NAME}] " '{ print prefix $0; fflush() }' &
  done
  # Los procesos en segundo plano ignoran Ctrl+C: se cierran desde aquí.
  trap 'pkill -P "$$" 2>/dev/null || true' INT TERM
  wait || wait || true
}

# Sólo limpia runners sin job en curso: Runner.Worker existe mientras corre un job.
prune_runner() {
  local worker_status=0
  if [[ -z "$(compose ps --status running --quiet runner)" ]]; then
    printf '%s: detenido, se omite.\n' "$RUNNER_NAME"
    return 0
  fi
  compose exec -T runner pgrep -f Runner.Worker >/dev/null || worker_status=$?
  case "$worker_status" in
    0) printf '%s: ejecutando un job, se omite.\n' "$RUNNER_NAME"; return 0 ;;
    1) ;;
    *) die "No se pudo comprobar si ${RUNNER_NAME} está ejecutando un job." ;;
  esac

  printf '%s: limpiando el Docker interno...\n' "$RUNNER_NAME"
  compose exec -T runner sh -c '
    docker container prune --force >/dev/null &&
    docker image prune --all --force &&
    docker network prune --force >/dev/null &&
    docker volume prune --force &&
    docker builder prune --all --force --filter until=168h &&
    docker system df'
}

prune_runners() {
  local index
  for ((index = 1; index <= runner_count; index++)); do
    select_runner "$index"
    prune_runner
  done
}

command_name="${1:-help}"
[[ "$#" -le 1 ]] || die 'Se admite un único comando. Consulta ./runner.sh help.' 2

case "$command_name" in
  help|-h|--help)
    usage
    exit 0
    ;;
  init|up|status|logs|stop|restart|prune|check) ;;
  *) usage >&2; die "Comando desconocido: ${command_name}" 2 ;;
esac

[[ "$runner_count" =~ ^[1-9]$ ]] || die 'RUNNER_COUNT debe ser un número entre 1 y 9.' 2

require_compose
if [[ "$command_name" == 'check' ]]; then
  select_runner 1
  compose config --quiet
  printf 'Compose válido. No se ha arrancado ni registrado ningún runner.\n'
  exit 0
fi

require_daemon
case "$command_name" in
  init) initialize_runners ;;
  up|restart)
    select_runner 1
    ensure_image
    require_registrations
    if [[ "$command_name" == 'restart' ]]; then
      for ((index = 1; index <= runner_count; index++)); do
        select_runner "$index"
        compose restart runner
      done
    fi
    start_runners
    ;;
  status) show_status ;;
  logs) follow_logs ;;
  stop)
    for ((index = 1; index <= runner_count; index++)); do
      select_runner "$index"
      compose stop runner
    done
    ;;
  prune) prune_runners ;;
esac
