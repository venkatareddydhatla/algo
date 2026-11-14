-- Seed roles + example India F&O underlyings (lot sizes: verify vs NSE circular)

INSERT INTO roles (name, description) VALUES
  ('admin', 'Full control including mode switch'),
  ('analyst', 'Signals, news, dry-run'),
  ('guest', 'Read-only dashboard')
ON CONFLICT (name) DO NOTHING;

INSERT INTO instruments (exchange, symbol, name, kind, lot_size, is_fno, tick_size)
VALUES
  ('NSE', 'NIFTY', 'Nifty 50', 'index', 25, TRUE, 0.05),
  ('NSE', 'BANKNIFTY', 'Nifty Bank', 'index', 15, TRUE, 0.05),
  ('NSE', 'RELIANCE', 'Reliance Industries', 'equity', 250, TRUE, 0.05),
  ('NSE', 'TCS', 'Tata Consultancy Services', 'equity', 175, TRUE, 0.05)
ON CONFLICT DO NOTHING;

INSERT INTO app_config_versions (key, value, version)
VALUES
  ('trading.mode', '"dry_run"', 1),
  ('trading.halt', 'false', 1)
ON CONFLICT DO NOTHING;
