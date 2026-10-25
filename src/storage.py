"""One owner process. Dashboard reads atomic public JSON, never the DuckDB writer."""
import logging
import os
import re
import threading
from pathlib import Path
import duckdb
import pandas as pd

def identifier(name):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
        raise ValueError('Invalid SQL identifier')
    return name

class Storage:
    def __init__(self, path='data/eleicoes.duckdb', use_hardcharger=True):
        self.lock = threading.RLock()
        self.backend = 'DuckDB'
        self.session = None
        if use_hardcharger and os.getenv('HARDCHARGER_URI'):
            try:
                from sqlframe_hardcharger import HardchargerSession
                import adbc_driver_hardcharger.dbapi as hc
                uri = os.environ['HARDCHARGER_URI']
                user, password = os.getenv('HARDCHARGER_USER', ''), os.getenv('HARDCHARGER_PASSWORD', '')
                self.session = (HardchargerSession.builder.config('hardcharger.uri', uri)
                                .config('hardcharger.username', user).config('hardcharger.password', password).getOrCreate())
                self.session.sql('SELECT 1').collect()
                self.conn = hc.connect(uri=uri, username=user, password=password)
                self.backend = 'Hardcharger / ADBC'
            except Exception as exc:
                # Exception text can contain connection credentials: log type only.
                logging.warning('Hardcharger unavailable (%s); startup fallback to DuckDB', type(exc).__name__)
                self.session = None
        if not self.session:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.conn = duckdb.connect(str(path))
        logging.info('STORAGE backend=%s', self.backend)

    def exists(self, table):
        return not self.query('SELECT table_name FROM information_schema.tables WHERE table_name = ?', [identifier(table)]).empty

    def ingest(self, df, table):
        if df.empty:
            return
        table = identifier(table)
        with self.lock:
            if self.session:
                self.session.ingest(table_name=table, data=df, mode='create_append')
            else:
                self.conn.register('_incoming', df)
                try:
                    if not self.exists(table):
                        self.conn.execute(f'CREATE TABLE {table} AS SELECT * FROM _incoming')
                    else:
                        self.conn.execute(f'INSERT INTO {table} BY NAME SELECT * FROM _incoming')
                finally:
                    self.conn.unregister('_incoming')

    def query(self, sql, params=None):
        with self.lock:
            if self.session:
                with self.conn.cursor() as cur:
                    cur.execute(sql, params) if params else cur.execute(sql)
                    return cur.fetch_arrow_table().to_pandas()
            return self.conn.execute(sql, params or []).fetchdf()

    def close(self):
        with self.lock:
            self.conn.close()
            if self.session:
                self.session.stop()
