-- Runs once, when the Postgres volume is first created.
CREATE DATABASE openfga OWNER vault;
-- A separate database for the test suite, so tests never touch your data.
CREATE DATABASE vault_test OWNER vault;
