-- Executed once by the postgres container on first start (docker-entrypoint-initdb.d).
-- Main DB `docintel` is created by POSTGRES_DB; these are the per-engineer test databases (TEAM.md § Tests).
CREATE DATABASE docintel_test_db OWNER di;
CREATE DATABASE docintel_test_be1 OWNER di;
CREATE DATABASE docintel_test_be2 OWNER di;
CREATE DATABASE docintel_test_qa OWNER di;
