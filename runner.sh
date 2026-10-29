#!/usr/bin/env bash
set -Eeuo pipefail

package_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly package_dir
readonly compose_file="${package_dir}/compose.yaml"
readonly state_volume='xiberty-gh-runner-state'
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
Uso: ./runner.sh <comando>

  init     Registrar una vez con un token temporal y arrancar en segundo plano.
  up       Arrancar usando el registro guardado; no pide token.
  status   Mostrar estado y salud del contenedor.
  logs     Seguir los logs; Ctrl+C sólo cierra el visor.
  stop     Detener el runner conservando sus datos.
  restart  Reiniciar el runner y esperar a su Docker interno.
  check    Validar el archivo Compose sin arrancar el runner.

Antes de stop o restart, espera a que GitHub muestre el runner como Idle.
USAGE
}

die() {
  printf '%s\n' "$1" >&2
  exit "${2:-1}"
}

compose() {
  docker compose --project-directory "$package_dir" --env-file /dev/null \
    -f "$compose_file" "$@"
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
  if ! grep -Fx "$state_volume" <<< "$volume_names" >/dev/null; then
    return 1
  fi

  # Sólo comprueba que existe el registro: no lee ni imprime las credenciales.
  docker run --rm --network none --read-only --cap-drop ALL \
    --security-opt no-new-privileges \
    --mount "type=volume,src=${state_volume},dst=/state,readonly" \
    --entrypoint /bin/sh "$image_name" -c 'test -s /state/.runner' || probe_status=$?
  case "$probe_status" in
    0) return 0 ;;
    1) return 1 ;;
    *) die 'No se pudo comprobar el registro guardado; revisa Docker.' ;;
  esac
}

start_runner() {
  compose up -d --wait --wait-timeout 180 runner
  printf '\nRunner arrancado. Comprueba que figure Idle en GitHub.\n'
  compose ps runner
}

initialize_runner() {
  ensure_image
  if is_registered; then
    printf 'Ya existe un registro guardado; se reutilizará sin pedir token.\n'
    start_runner
    return
  fi

  if [[ -z "$bootstrap_token" ]]; then
    printf 'Genera un token nuevo en xiberty > Settings > Actions > Runners.\n'
    if ! IFS= read -r -s -p 'Token temporal de registro: ' bootstrap_token </dev/tty; then
      die 'Ejecuta init desde una terminal, o proporciona RUNNER_TOKEN en su entorno.'
    fi
    printf '\n'
  fi
  [[ -n "$bootstrap_token" && "$bootstrap_token" != *[[:space:]]* ]] \
    || die 'El token está vacío o contiene espacios/saltos de línea.'

  # El token sólo llega a este contenedor de alta, que se elimina al terminar.
  # El entrypoint de la imagen registra y guarda las credenciales antes de /bin/true.
  export RUNNER_TOKEN="$bootstrap_token"
  compose run --rm --no-deps -T -e START_DOCKER_SERVICE=false runner /bin/true
  unset RUNNER_TOKEN bootstrap_token

  is_registered || die 'El alta no dejó un registro válido. Consulta el error anterior.'
  start_runner
}

command_name="${1:-help}"
[[ "$#" -le 1 ]] || die 'Se admite un único comando. Consulta ./runner.sh help.' 2

case "$command_name" in
  help|-h|--help)
    usage
    exit 0
    ;;
  init|up|status|logs|stop|restart|check) ;;
  *) usage >&2; die "Comando desconocido: ${command_name}" 2 ;;
esac

require_compose
if [[ "$command_name" == 'check' ]]; then
  compose config --quiet
  printf 'Compose válido. No se ha arrancado ni registrado ningún runner.\n'
  exit 0
fi

require_daemon
case "$command_name" in
  init) initialize_runner ;;
  up|restart)
    ensure_image
    is_registered || die 'No existe un registro guardado. Ejecuta ./runner.sh init.'
    if [[ "$command_name" == 'restart' ]]; then
      compose restart runner
    fi
    start_runner
    ;;
  status) compose ps runner ;;
  logs) compose logs --tail 100 --follow runner ;;
  stop) compose stop runner ;;
esac
