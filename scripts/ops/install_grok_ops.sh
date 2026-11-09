#!/bin/bash
# Install the grok-ops forced-command user, sudoers drop-in, and verb scripts.
# Root ignores AFS_OPS_* and cwd. The interpreter is /usr/bin/python3.
# A non-root run is the test prefix only, and it still cannot choose the interpreter.
set -euo pipefail

die() {
  echo "$1" >&2
  exit "${2:-1}"
}

walk_real() {
  local path="$1"
  local cursor=""
  local part
  [[ "$path" == /* ]] || die "refusing a path that depends on cwd" 2
  local IFS=/
  local -a parts
  read -r -a parts <<< "$path"
  for part in "${parts[@]}"; do
    [[ -z "$part" ]] && continue
    [[ "$part" == "." || "$part" == ".." ]] && die "refusing a parent segment" 2
    cursor="${cursor}/${part}"
    if [[ -L "$cursor" ]]; then
      die "refusing symlink: $cursor" 2
    fi
  done
  [[ -e "$cursor" ]] || die "missing path" 2
}

PUBLIC_KEY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --public-key)
      PUBLIC_KEY="${2:-}"
      shift 2
      ;;
    *)
      die "usage: install_grok_ops.sh --public-key KEYFILE" 2
      ;;
  esac
done

SCRIPT=${BASH_SOURCE[0]}
walk_real "$SCRIPT"
REPO=$(dirname "$(dirname "$(dirname "$SCRIPT")")")
walk_real "$REPO"
[[ "$SCRIPT" == "$REPO/scripts/ops/install_grok_ops.sh" ]] || die "unexpected script location" 2
[[ -n "$PUBLIC_KEY" ]] || die "usage: install_grok_ops.sh --public-key KEYFILE" 2
walk_real "$PUBLIC_KEY"
[[ -f "$PUBLIC_KEY" && ! -L "$PUBLIC_KEY" ]] || die "usage: install_grok_ops.sh --public-key KEYFILE" 2

if [[ "$(id -u)" -eq 0 ]]; then
  unset AFS_OPS_ROOT AFS_OPS_PYTHON AFS_PYTHON AFS_OPS_FLAT_FILE PYTHONPATH
  PREFIX=""
else
  PREFIX="${AFS_OPS_ROOT:-}"
  [[ -n "$PREFIX" ]] || die "install_grok_ops.sh must run as root" 1
  unset AFS_OPS_PYTHON AFS_PYTHON PYTHONPATH
fi

PY=/usr/bin/python3
[[ -x "$PY" ]] || die "missing /usr/bin/python3" 1

if find "$REPO/ops/grok_ops" -type l | grep -q .; then
  die "refusing symlink in ops/grok_ops" 2
fi

run_py() {
  env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$REPO" \
    "$PY" -B "$@"
}

install_file() {
  local mode="$1" src="$2" dest="$3"
  mkdir -p "$(dirname "$dest")"
  cp -P "$src" "$dest"
  chmod "$mode" "$dest"
}

if [[ -n "$PREFIX" ]]; then
  mkdir -p "$PREFIX/etc"
  if [[ ! -f "$PREFIX/etc/passwd" ]] || ! grep -q '^grok-ops:' "$PREFIX/etc/passwd"; then
    printf '%s\n' 'grok-ops:x:900:900:AFS grok-ops:/var/lib/afs-ops:/usr/sbin/nologin' >> "$PREFIX/etc/passwd"
  fi
else
  if ! id grok-ops >/dev/null 2>&1; then
    useradd --system --create-home --home-dir /var/lib/afs-ops --shell /usr/sbin/nologin grok-ops
  fi
  passwd -l grok-ops >/dev/null
fi

staged="$(mktemp)"
trap 'rm -f "$staged"' EXIT
run_py -m ops.grok_ops.sudoers > "$staged"
visudo -c -f "$staged" >/dev/null
install_file 440 "$staged" "$PREFIX/etc/sudoers.d/grok-ops"

key_line="$(run_py -m ops.grok_ops.keys --public-key "$PUBLIC_KEY")"
key_dir="$PREFIX/var/lib/afs-ops/.ssh"
mkdir -p "$key_dir" "$PREFIX/var/lib/afs-ops/tokens" "$PREFIX/etc/afs-ops" "$PREFIX/usr/local/sbin"
printf '%s\n' "$key_line" > "$key_dir/authorized_keys"
chmod 750 "$PREFIX/var/lib/afs-ops"
chmod 700 "$key_dir" "$PREFIX/var/lib/afs-ops/tokens"
chmod 600 "$key_dir/authorized_keys"
if [[ ! -f "$PREFIX/var/lib/afs-ops/audit.log" ]]; then
  : > "$PREFIX/var/lib/afs-ops/audit.log"
fi
chmod 640 "$PREFIX/var/lib/afs-ops/audit.log"

if [[ ! -f "$PREFIX/etc/afs-ops/release.env" ]]; then
  cat > "$PREFIX/etc/afs-ops/release.env" <<'EOF'
# Root-owned invocation config for scripts/atomic_release.sh.
# Set AFS_BOX to the sanctioned release target. Do not put trading secrets here.
AFS_BOX=
EOF
  chmod 600 "$PREFIX/etc/afs-ops/release.env"
fi

lib="$PREFIX/usr/local/lib/afs-ops"
rm -rf "$lib"
mkdir -p "$lib/ops"
cp -P "$REPO/ops/__init__.py" "$lib/ops/__init__.py"
cp -R -P "$REPO/ops/grok_ops" "$lib/ops/grok_ops"
chmod -R u+rX,go-w "$lib"

write_root() {
  local name="$1"
  local body="$2"
  local dest="$PREFIX/usr/local/sbin/$name"
  printf '%s\n' '#!/bin/sh' "$body" > "$dest"
  chmod 755 "$dest"
}

root_exec='/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/usr/local/lib/afs-ops /usr/bin/python3 -B -m'
write_root afs-ops-preflight "exec $root_exec ops.grok_ops.actions preflight"
write_root afs-ops-backup "exec $root_exec ops.grok_ops.actions backup"
write_root afs-ops-baseline-reset-dry-run "exec $root_exec ops.grok_ops.actions baseline-reset-dry-run"
write_root afs-ops-baseline-reset "exec $root_exec ops.grok_ops.actions baseline-reset"
write_root afs-ops-promote "exec $root_exec ops.grok_ops.actions promote"
write_root afs-ops-verify "exec $root_exec ops.grok_ops.actions verify"
write_root afs-ops-rollback "exec $root_exec ops.grok_ops.actions rollback"
write_root afs-ops-status "exec $root_exec ops.grok_ops.actions status"
write_root afs-ops-record-refusal "exec $root_exec ops.grok_ops.dispatcher record-refusal"
write_root afs-ops-authorize "exec $root_exec ops.grok_ops.authorize \"\$@\""
write_root afs-grok-ops "exec /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/usr/local/lib/afs-ops SSH_ORIGINAL_COMMAND=\"\$SSH_ORIGINAL_COMMAND\" SSH_CLIENT=\"\$SSH_CLIENT\" /usr/bin/python3 -B -m ops.grok_ops.dispatcher"

if [[ -z "$PREFIX" ]]; then
  chown -R grok-ops:grok-ops /var/lib/afs-ops/.ssh
  chown root:root /var/lib/afs-ops/tokens /var/lib/afs-ops/audit.log /etc/sudoers.d/grok-ops
  chown root:grok-ops /var/lib/afs-ops
fi

echo "grok-ops lane installed"
