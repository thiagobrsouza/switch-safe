#!/usr/bin/env bash
# Teste ponta a ponta do Switch Safe em ambiente isolado (projeto "switch-safe-test").
# Sobe app + switch simulado, executa os testes e remove tudo ao final.
set -u
cd "$(dirname "$0")"
COMPOSE="docker compose -f docker-compose.test.yml"

$COMPOSE build app
$COMPOSE run --rm tester
status=$?
$COMPOSE down -v --remove-orphans >/dev/null 2>&1
exit $status
