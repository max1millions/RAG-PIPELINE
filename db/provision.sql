-- Run once as MySQL root after installing mysql-server:
--   sudo mysql < ~/.openclaw/workspace/RAG-PIPELINE/db/provision.sql
--
-- The database name (orion_app) must match MYSQL_DATABASE in your overlay .env.
-- To use a different name, replace orion_app below and update MYSQL_DATABASE.
--
-- Then set the same password in ORION_OVERLAY_ROOT/config/.env (MYSQL_PASSWORD).

CREATE DATABASE IF NOT EXISTS orion_app
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

-- Replace CHANGE_ME_ORION_LOCAL with your chosen password before running,
-- or run interactively and substitute.
CREATE USER IF NOT EXISTS 'orion'@'localhost' IDENTIFIED BY 'CHANGE_ME_ORION_LOCAL';
CREATE USER IF NOT EXISTS 'orion'@'127.0.0.1' IDENTIFIED BY 'CHANGE_ME_ORION_LOCAL';

GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, INDEX, ALTER, CREATE TEMPORARY TABLES, LOCK TABLES, EXECUTE, CREATE VIEW, SHOW VIEW, TRIGGER
  ON orion_app.*
  TO 'orion'@'localhost', 'orion'@'127.0.0.1';

FLUSH PRIVILEGES;
