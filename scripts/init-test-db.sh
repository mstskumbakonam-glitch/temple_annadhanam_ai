#!/bin/bash
# Runs once, when PostgreSQL initialises an empty data volume.
# Creates the separate database used by pytest so tests never touch production data.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    SELECT 'CREATE DATABASE ${POSTGRES_DB}_test'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${POSTGRES_DB}_test')\gexec
EOSQL

echo "Test database ${POSTGRES_DB}_test is ready."
