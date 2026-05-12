#!/bin/bash
# Create the autosending database if it doesn't exist.
# The main gramly database is created by POSTGRES_DB env var automatically.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
  SELECT 'CREATE DATABASE autosending'
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'autosending')\gexec
  GRANT ALL PRIVILEGES ON DATABASE autosending TO $POSTGRES_USER;
EOSQL
