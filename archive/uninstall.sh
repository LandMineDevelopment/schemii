#!/usr/bin/env bash
set -euo pipefail

assume_yes=0
case "${1:-}" in
  --yes|-y) assume_yes=1 ;;
  "") ;;
  *) printf 'Usage: bash ./uninstall.sh [--yes]\n' >&2; exit 2 ;;
esac

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ "$repo_dir" == "/" || "$repo_dir" == "${HOME:-}" \
  || ! -f "$repo_dir/compose.yaml" || ! -f "$repo_dir/start.sh" \
  || ! -d "$repo_dir/src/schemii" ]]; then
  printf 'Refusing to remove %s because it is not a recognized Schemii repository.\n' "$repo_dir" >&2
  exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
  printf 'Docker was not found. Install or restore Docker first so Schemii containers and volumes can be removed safely.\n' >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  printf 'Docker is unavailable or access was denied. Start Docker and run docker info before uninstalling Schemii.\n' >&2
  exit 1
fi

credential_root="${SCHEMII_CREDENTIAL_ROOT:-${XDG_DATA_HOME:-${HOME:?HOME is required}/.local/share}/schemii/credentials}"
if [[ "$credential_root" != /* || ( -n "${SCHEMII_CREDENTIAL_DIR:-}" && "$SCHEMII_CREDENTIAL_DIR" != /* ) ]]; then
  printf 'SCHEMII_CREDENTIAL_ROOT and SCHEMII_CREDENTIAL_DIR must be absolute paths.\n' >&2
  exit 2
fi
volume_suffixes=(
  schemii-config schemii-schemas schemii-postgres schemii-metadata-postgres
  schemii-opencode-data schemii-opencode-config schemii-opencode-state schemii-opencode-cache
  schemer-dashboards schemii-recovery host-postgres-socket
)

valid_project() { [[ "$1" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; }
known_volume() {
  local candidate="$1" suffix
  for suffix in "${volume_suffixes[@]}"; do
    [[ "$candidate" == "$suffix" ]] && return 0
  done
  return 1
}
credential_matches() {
  local project="$1" directory marker="" extra="" had_lf=0
  directory="${SCHEMII_CREDENTIAL_DIR:-$credential_root/$project}"
  [[ -f "$directory/instance" ]] || return 1
  exec 3< "$directory/instance"
  if IFS= read -r marker <&3; then had_lf=1; fi
  if [[ "$had_lf" == "1" ]] && { IFS= read -r extra <&3 || [[ -n "$extra" ]]; }; then
    exec 3<&-
    return 1
  fi
  exec 3<&-
  [[ "$marker" == "$project" ]]
}
legacy_identity_name=""
legacy_identity_created_at=""
legacy_identity_driver=""
legacy_identity_mountpoint=""
legacy_identity_scope=""
legacy_identity_labels=""
legacy_identity_raw=""
legacy_config_identity_raw=""
legacy_schemas_identity_raw=""
legacy_config_manifest_checksum=""
legacy_schemas_manifest_checksum=""
inspect_legacy_volume_identity() {
  local project="$1" logical="$2" volume="${1}_${2}" details
  if ! details="$(docker volume inspect --format '{{.Name}}|{{.CreatedAt}}|{{.Driver}}|{{.Mountpoint}}|{{.Scope}}|{{json .Labels}}' "$volume" 2>/dev/null)"; then
    return 1
  fi
  legacy_identity_raw="$details"
  IFS='|' read -r legacy_identity_name legacy_identity_created_at legacy_identity_driver \
    legacy_identity_mountpoint legacy_identity_scope legacy_identity_labels <<< "$details"
  [[ "$legacy_identity_name" == "$volume" \
    && -n "$legacy_identity_created_at" \
    && "$legacy_identity_driver" == "local" \
    && -n "$legacy_identity_mountpoint" \
    && "$legacy_identity_scope" == "local" \
    && ( "$legacy_identity_labels" == "null" || "$legacy_identity_labels" == "{}" ) ]]
}
legacy_manifest_body() {
  local project="$1" logical="$2" name="$3" created_at="$4" driver="$5" mountpoint="$6" scope="$7"
  printf '%s\n' \
    'format=schemii-legacy-volume-adoption-v1' \
    "project=$project" \
    "repository=$repo_dir" \
    "logical=$logical" \
    "volume=$name" \
    "created-at=$created_at" \
    "driver=$driver" \
    "mountpoint=$mountpoint" \
    "scope=$scope"
}
owner_only_path() {
  local path="$1" expected_mode="$2" details=""
  if details="$(stat -c '%u|%a' -- "$path" 2>/dev/null)"; then
    :
  elif details="$(stat -f '%u|%Lp' "$path" 2>/dev/null)"; then
    :
  else
    return 1
  fi
  [[ "$details" == "$(id -u)|$expected_mode" ]]
}
read_lock_marker() {
  local value="" extra=""
  [[ -f "$1" && ! -L "$1" ]] || { printf '%s' "$value"; return; }
  exec 9< "$1"
  if ! IFS= read -r value <&9; then exec 9<&-; printf '%s' ''; return; fi
  if IFS= read -r extra <&9 || [[ -n "$extra" ]]; then exec 9<&-; printf '%s' ''; return; fi
  exec 9<&-
  printf '%s' "$value"
}
legacy_adoption_set_matches() {
  local project="$1" directory adoption_dir logical path entry name count=0
  [[ "$project" == "schemii" ]] || return 1
  credential_matches "$project" || return 1
  directory="${SCHEMII_CREDENTIAL_DIR:-$credential_root/$project}"
  adoption_dir="$directory/legacy-volume-adoptions.v1"
  [[ -d "$adoption_dir" && ! -L "$adoption_dir" ]] || return 1
  owner_only_path "$adoption_dir" 700 || return 1
  for entry in "$adoption_dir"/* "$adoption_dir"/.[!.]* "$adoption_dir"/..?*; do
    [[ -e "$entry" || -L "$entry" ]] || continue
    name="$(basename -- "$entry")"
    case "$name" in
      schemii-config.manifest|schemii-schemas.manifest) ;;
      *) return 1 ;;
    esac
    count=$((count + 1))
  done
  [[ "$count" == "2" ]] || return 1
  for logical in schemii-config schemii-schemas; do
    inspect_legacy_volume_identity "$project" "$logical" || return 1
    path="$adoption_dir/$logical.manifest"
    [[ -f "$path" && ! -L "$path" ]] || return 1
    owner_only_path "$path" 600 || return 1
    cmp -s -- "$path" <(legacy_manifest_body "$project" "$logical" "$legacy_identity_name" \
      "$legacy_identity_created_at" "$legacy_identity_driver" "$legacy_identity_mountpoint" "$legacy_identity_scope") \
      || return 1
    case "$logical" in
      schemii-config)
        legacy_config_identity_raw="$legacy_identity_raw"
        legacy_config_manifest_checksum="$(cksum < "$path")" || return 1
        ;;
      schemii-schemas)
        legacy_schemas_identity_raw="$legacy_identity_raw"
        legacy_schemas_manifest_checksum="$(cksum < "$path")" || return 1
        ;;
    esac
  done
}
legacy_resource_still_attested() {
  local project="$1" logical="$2" directory adoption_dir entry name count=0 current_checksum expected_checksum expected_identity
  [[ "$project" == "schemii" && ( "$logical" == "schemii-config" || "$logical" == "schemii-schemas" ) ]] || return 1
  credential_matches "$project" || return 1
  directory="${SCHEMII_CREDENTIAL_DIR:-$credential_root/$project}"
  adoption_dir="$directory/legacy-volume-adoptions.v1"
  [[ -d "$adoption_dir" && ! -L "$adoption_dir" ]] || return 1
  owner_only_path "$adoption_dir" 700 || return 1
  for entry in "$adoption_dir"/* "$adoption_dir"/.[!.]* "$adoption_dir"/..?*; do
    [[ -e "$entry" || -L "$entry" ]] || continue
    name="$(basename -- "$entry")"
    case "$name" in
      schemii-config.manifest|schemii-schemas.manifest) ;;
      *) return 1 ;;
    esac
    count=$((count + 1))
  done
  [[ "$count" == "2" ]] || return 1
  for name in schemii-config schemii-schemas; do
    entry="$adoption_dir/$name.manifest"
    [[ -f "$entry" && ! -L "$entry" ]] || return 1
    owner_only_path "$entry" 600 || return 1
    current_checksum="$(cksum < "$entry")" || return 1
    if [[ "$name" == "schemii-config" ]]; then
      expected_checksum="$legacy_config_manifest_checksum"
    else
      expected_checksum="$legacy_schemas_manifest_checksum"
    fi
    [[ -n "$expected_checksum" && "$current_checksum" == "$expected_checksum" ]] || return 1
  done
  inspect_legacy_volume_identity "$project" "$logical" || return 1
  if [[ "$logical" == "schemii-config" ]]; then
    expected_identity="$legacy_config_identity_raw"
  else
    expected_identity="$legacy_schemas_identity_raw"
  fi
  [[ -n "$expected_identity" && "$legacy_identity_raw" == "$expected_identity" ]]
}

approved_projects=()
orphan_volume_projects=()
orphan_volume_counts=()
orphan_volume_keys=()
owned_image_references=()
owned_image_ids=()
add_approved_project() {
  local candidate="$1" existing index
  for (( index=0; index<${#approved_projects[@]}; index++ )); do
    existing="${approved_projects[$index]}"
    [[ "$existing" == "$candidate" ]] && return 0
  done
  approved_projects+=("$candidate")
}
record_orphan_volume() {
  local project="$1" logical_name="$2" key="$1:$2" index
  for (( index=0; index<${#orphan_volume_keys[@]}; index++ )); do
    [[ "${orphan_volume_keys[$index]}" == "$key" ]] && return 0
  done
  orphan_volume_keys+=("$key")
  for (( index=0; index<${#orphan_volume_projects[@]}; index++ )); do
    if [[ "${orphan_volume_projects[$index]}" == "$project" ]]; then
      orphan_volume_counts[$index]=$(( ${orphan_volume_counts[$index]} + 1 ))
      return 0
    fi
  done
  orphan_volume_projects+=("$project")
  orphan_volume_counts+=(1)
}
record_owned_image() {
  local reference="$1" image_id="$2" index
  for (( index=0; index<${#owned_image_references[@]}; index++ )); do
    if [[ "${owned_image_references[$index]}" == "$reference" ]]; then
      owned_image_ids[$index]="$image_id"
      return 0
    fi
  done
  owned_image_references+=("$reference")
  owned_image_ids+=("$image_id")
}

all_container_ids=()
while IFS= read -r container_id; do
  [[ -n "$container_id" ]] && all_container_ids+=("$container_id")
done < <(docker ps -aq)
for (( container_index=0; container_index<${#all_container_ids[@]}; container_index++ )); do
  container_id="${all_container_ids[$container_index]}"
  labels="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}|{{ index .Config.Labels "com.docker.compose.service" }}|{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$container_id" 2>/dev/null || true)"
  IFS='|' read -r project service working_dir <<< "$labels"
  if valid_project "$project" && [[ "$service" == "schemii" || "$service" == "schemer" ]] \
    && [[ "$working_dir" == "$repo_dir" ]]; then
    add_approved_project "$project"
  fi
done

all_volumes=()
while IFS= read -r volume; do
  [[ -n "$volume" ]] && all_volumes+=("$volume")
done < <(docker volume ls -q)
for (( volume_index=0; volume_index<${#all_volumes[@]}; volume_index++ )); do
  volume="${all_volumes[$volume_index]}"
  labels="$(docker volume inspect --format '{{ index .Labels "com.docker.compose.project" }}|{{ index .Labels "com.docker.compose.volume" }}' "$volume" 2>/dev/null || true)"
  IFS='|' read -r project logical_name <<< "$labels"
  if valid_project "$project" && known_volume "$logical_name" \
    && [[ "$volume" == "${project}_${logical_name}" ]]; then
    record_orphan_volume "$project" "$logical_name"
  fi
done
for (( index=0; index<${#orphan_volume_projects[@]}; index++ )); do
  project="${orphan_volume_projects[$index]}"
  if (( orphan_volume_counts[index] >= 2 )) || credential_matches "$project"; then
    add_approved_project "$project"
  fi
done
if legacy_adoption_set_matches schemii; then
  add_approved_project schemii
fi

project_list=()
if [[ ${#approved_projects[@]} -gt 0 ]]; then
  while IFS= read -r project; do
    [[ -n "$project" ]] && project_list+=("$project")
  done < <(printf '%s\n' "${approved_projects[@]}" | sort)
fi

printf 'This permanently removes:\n'
printf '  - every verified Schemii Docker container and network\n'
printf '  - all verified Schemii designs, profiles, passwords, migration history, PostgreSQL data, AI credentials, and chats\n'
printf '  - safely attributable project-scoped Schemii images\n'
printf '  - each verified instance credential directory\n'
printf '  - repository: %s\n' "$repo_dir"
if [[ ${#project_list[@]} -gt 0 ]]; then
  printf 'Detected Schemii instances:\n'
  for project in "${project_list[@]}"; do printf '  - %s\n' "$project"; done
else
  printf 'Detected Schemii instances: none\n'
fi
printf 'Unrelated or ambiguously owned Docker projects, images, and volumes are not removed.\n'

if [[ "$assume_yes" != "1" ]]; then
  printf 'Type UNINSTALL to continue: '
  IFS= read -r confirmation
  if [[ "$confirmation" != "UNINSTALL" ]]; then
    printf 'Uninstall cancelled. Nothing was removed.\n'
    exit 1
  fi
fi

for (( project_index=0; project_index<${#project_list[@]}; project_index++ )); do
  project="${project_list[$project_index]}"
  legacy_attested=0
  if legacy_adoption_set_matches "$project"; then legacy_attested=1; fi
  owned_container_ids=()
  for (( container_index=0; container_index<${#all_container_ids[@]}; container_index++ )); do
    container_id="${all_container_ids[$container_index]}"
    labels="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}|{{ index .Config.Labels "com.docker.compose.service" }}|{{ index .Config.Labels "com.docker.compose.project.working_dir" }}|{{.Image}}|{{.Config.Image}}' "$container_id" 2>/dev/null || true)"
    IFS='|' read -r resource_project service working_dir image_id image_reference <<< "$labels"
    if [[ "$resource_project" == "$project" && "$working_dir" == "$repo_dir" ]]; then
      owned_container_ids+=("$container_id")
      case "$image_reference" in
        "schemii:$project"|"schemii-metadata-postgres:$project"|"schemii-opencode:1.18.15-$project")
          [[ -n "$image_id" ]] && record_owned_image "$image_reference" "$image_id"
          ;;
      esac
    fi
  done
  if [[ ${#owned_container_ids[@]} -gt 0 ]]; then
    docker rm -f "${owned_container_ids[@]}"
  fi

  network_ids=()
  while IFS= read -r network_id; do
    [[ -n "$network_id" ]] && network_ids+=("$network_id")
  done < <(docker network ls -q --filter "label=com.docker.compose.project=$project")
  for (( network_index=0; network_index<${#network_ids[@]}; network_index++ )); do
    network_id="${network_ids[$network_index]}"
    labels="$(docker network inspect --format '{{ index .Labels "com.docker.compose.project" }}|{{ index .Labels "com.docker.compose.network" }}|{{.Name}}' "$network_id" 2>/dev/null || true)"
    IFS='|' read -r resource_project logical_name resource_name <<< "$labels"
    if [[ "$resource_project" == "$project" \
      && ( "$logical_name" == "default" || "$logical_name" == "schemii-ingress" || "$logical_name" == "schemer-ingress" || "$logical_name" == "schemii-loopback" || "$logical_name" == "schemer-loopback" ) \
      && "$resource_name" == "${project}_${logical_name}" ]]; then
      docker network rm "$network_id"
    fi
  done

  for (( volume_index=0; volume_index<${#all_volumes[@]}; volume_index++ )); do
    volume="${all_volumes[$volume_index]}"
    labels="$(docker volume inspect --format '{{ index .Labels "com.docker.compose.project" }}|{{ index .Labels "com.docker.compose.volume" }}|{{.Name}}' "$volume" 2>/dev/null || true)"
    IFS='|' read -r resource_project logical_name resource_name <<< "$labels"
    if [[ "$resource_project" == "$project" ]] && known_volume "$logical_name" \
      && [[ "$resource_name" == "${project}_${logical_name}" && "$volume" == "$resource_name" ]]; then
      docker volume rm "$volume"
    elif [[ "$legacy_attested" == "1" && "$project" == "schemii" \
      && ( "$volume" == "schemii_schemii-config" || "$volume" == "schemii_schemii-schemas" ) ]]; then
      logical_name="${volume#schemii_}"
      if legacy_resource_still_attested "$project" "$logical_name"; then
        docker volume rm "$volume"
      else
        printf 'Legacy adoption evidence or volume identity changed during uninstall; remaining data and credentials were preserved.\n' >&2
        exit 1
      fi
    fi
  done
done

for (( index=0; index<${#owned_image_references[@]}; index++ )); do
  image_reference="${owned_image_references[$index]}"
  image_id="${owned_image_ids[$index]}"
  current_id="$(docker image inspect --format '{{.Id}}' "$image_reference" 2>/dev/null || true)"
  image_users_output=""
  if [[ -n "$image_id" && "$current_id" == "$image_id" ]] \
    && image_users_output="$(docker ps -aq --filter "ancestor=$image_id")"; then
    :
  else
    image_users_output=unknown
  fi
  if [[ -z "$image_users_output" ]]; then
    docker image rm "$image_reference"
  fi
done

for project in "${project_list[@]}"; do
  credential_dir="${SCHEMII_CREDENTIAL_DIR:-$credential_root/$project}"
  if credential_matches "$project"; then
    rm -rf -- "$credential_dir"
  fi
done

repo_parent="$(dirname -- "$repo_dir")"
repo_name="$(basename -- "$repo_dir")"
printf 'Verified Docker resources removed. Removing repository %s\n' "$repo_dir"
cd -- "$repo_parent"
rm -rf -- "$repo_name"
printf 'Schemii has been uninstalled.\n'
