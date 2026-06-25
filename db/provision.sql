-- One-time MySQL root provisioning (e.g. sudo mysql < .../db/provision.sql).
--
-- Database name (orion_app) should match MYSQL_DATABASE in overlay .env.
-- To use a different name, change orion_app here and in MYSQL_DATABASE.
--
-- MYSQL_PASSWORD in ORION_OVERLAY_ROOT/config/.env should match the user password below.

CREATE DATABASE IF NOT EXISTS orion_app
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

-- Default password placeholder CHANGE_ME_ORION_LOCAL; must match MYSQL_PASSWORD in overlay .env.
CREATE USER IF NOT EXISTS 'orion'@'localhost' IDENTIFIED BY 'CHANGE_ME_ORION_LOCAL';
CREATE USER IF NOT EXISTS 'orion'@'127.0.0.1' IDENTIFIED BY 'CHANGE_ME_ORION_LOCAL';

GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, INDEX, ALTER, CREATE TEMPORARY TABLES, LOCK TABLES, EXECUTE, CREATE VIEW, SHOW VIEW, TRIGGER
  ON orion_app.*
  TO 'orion'@'localhost', 'orion'@'127.0.0.1';

FLUSH PRIVILEGES;
