"""Migração mínima de esquema para SQLite.

db.create_all() cria tabelas novas, mas não adiciona colunas novas em tabelas
existentes. Este módulo compara os modelos com o banco e executa
ALTER TABLE ... ADD COLUMN para o que estiver faltando. Colunas NOT NULL
precisam de server_default.
"""

import logging

from sqlalchemy import inspect, text

log = logging.getLogger(__name__)


def auto_migrate(db):
    engine = db.engine
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in db.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column.type.compile(dialect=engine.dialect)}'
                if column.server_default is not None:
                    ddl += f" DEFAULT {column.server_default.arg}"
                elif not column.nullable:
                    log.error("Coluna %s.%s é NOT NULL sem server_default; não migrada.", table.name, column.name)
                    continue
                conn.execute(text(ddl))
                log.info("Migração: coluna %s.%s adicionada.", table.name, column.name)
