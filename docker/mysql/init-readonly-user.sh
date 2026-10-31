#!/bin/sh
set -eu

readonly_user="${MYSQL_AGENT_READONLY_USER:?MYSQL_AGENT_READONLY_USER must be set}"
readonly_password="${MYSQL_AGENT_READONLY_PASSWORD:?MYSQL_AGENT_READONLY_PASSWORD must be set}"

case "${readonly_user}${readonly_password}" in
  *[!A-Za-z0-9_@%+=.-]*)
    echo "MYSQL_AGENT_READONLY_USER and MYSQL_AGENT_READONLY_PASSWORD contain unsupported characters."
    exit 1
    ;;
esac

mysql --protocol=socket -uroot -p"${MYSQL_ROOT_PASSWORD}" <<-EOSQL
CREATE USER IF NOT EXISTS '${readonly_user}'@'%' IDENTIFIED BY '${readonly_password}';
GRANT SELECT ON dw.* TO '${readonly_user}'@'%';
FLUSH PRIVILEGES;
EOSQL
