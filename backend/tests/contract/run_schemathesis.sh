#!/usr/bin/env bash
# QA: Schemathesis 4 contract run of api/openapi.yaml against a running backend (see docs/test-report.md § Contract).
#   DI_DATABASE_URL=…/docintel_test_qa python -m app.db.reset
#   DI_DATABASE_URL=…/docintel_test_qa DI_BOOTSTRAP_API_KEY=dik_test_admin DI_RUN_STEP_DELAY_MS=0 uvicorn app.main:app --port 8084 &
#   tests/contract/run_schemathesis.sh            # then stop uvicorn
# No operation is excluded. Destructive calls hit the QA database only (reset it afterwards).
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT=${QA_ST_OUT:-/tmp/qa_run/st}
mkdir -p "$OUT"
schemathesis run ../api/openapi.yaml --url "${QA_BASE_URL:-http://localhost:8084/v1}" \
  -H "X-API-Key: ${QA_API_KEY:-dik_test_admin}" \
  --max-examples "${QA_MAX_EXAMPLES:-10}" --seed "${QA_SEED:-4242}" \
  --checks not_a_server_error,status_code_conformance,content_type_conformance,response_schema_conformance \
  --phases examples,coverage,fuzzing --continue-on-failure --request-timeout 20 -w 4 \
  --report junit,ndjson --report-dir "$OUT" --no-color "$@"
