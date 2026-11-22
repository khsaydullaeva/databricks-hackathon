"""Read-only warehouse access and the production ranking entry point."""
import json
import os
import time
from typing import Protocol
from urllib.parse import urlsplit

from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from databricks.sdk.errors import DatabricksError
from databricks.sdk.service.sql import StatementParameterListItem

from careproof.recommend import recommend

GOLD = "workspace.careproof.gold_service_index"
VERIFIED = "workspace.careproof.verified_claims"
ADDRESS = "city_clean, state_clean, district_clean, pin_clean, pin_status"
ADDRESS_LOOKUP = ADDRESS + ", admin_status, pin_state_disagreement, country_disagreement"
GOLD_FIELDS = f"""facility_id, service_key, name, {ADDRESS},
    geo_facility_id, geo_status, admin_status, pin_state_disagreement,
    country_disagreement, candidate_latitude, candidate_longitude,
    coordinate_provenance, evidence_status, claim_type, claim_reason, has_service_negation,
    identity_review_count, clear_identity_review_count, identity_reason_codes,
    verification_reason, source_field, claim_text, source_text, source_content_id,
    matched_aliases, source_urls_raw, source_attribution_status"""
EVIDENCE_PAGE = 6
WarehouseRead = tuple[list[dict[str, object]], float]


class SearchDataSource(Protocol):
    """Data operations the app needs from a read-only connector."""

    def addresses(self) -> WarehouseRead: ...
    def service(self, service: str) -> WarehouseRead: ...
    def evidence(self, service: str, facility: str, offset: int) -> WarehouseRead: ...


class DataUnavailable(Exception):
    """Safe text for display in the app."""


def connect() -> SearchDataSource:
    warehouse = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    if not warehouse:
        raise DataUnavailable("The SQL warehouse is missing from the app configuration.")
    try:
        return Warehouse(WorkspaceClient(config=Config(http_timeout_seconds=8, retry_timeout_seconds=5)), warehouse)
    except (ValueError, DatabricksError, OSError):
        raise DataUnavailable("Databricks sign-in is unavailable. Check the configured identity.") from None


class Warehouse:
    def __init__(self, client, warehouse):
        self.sql = client.statement_execution
        self.warehouse = warehouse

    def read(self, statement, **bindings):
        started = time.monotonic()
        deadline = started + 35
        try:
            response = self.sql.execute_statement(
                warehouse_id=self.warehouse, statement=statement, wait_timeout="5s",
                parameters=[StatementParameterListItem(name=name, value=str(value),
                            type="INT" if isinstance(value, int) else "STRING")
                            for name, value in bindings.items()],
            )
            while response.status.state.value in {"PENDING", "RUNNING"}:
                if time.monotonic() >= deadline:
                    try:
                        self.sql.cancel_execution(response.statement_id)
                    except (DatabricksError, OSError, ValueError):
                        pass
                    raise DataUnavailable("The warehouse took too long to respond. It may be starting. Run the search again in a moment.")
                time.sleep(0.75)
                response = self.sql.get_statement(response.statement_id)
            if response.status.state.value != "SUCCEEDED":
                code = getattr(response.status.error, "error_code", None)
                if getattr(code, "value", code) in {"PERMISSION_DENIED", "UNAUTHENTICATED"}:
                    raise DataUnavailable("The app identity needs access to the warehouse and CareProof tables.")
                raise DataUnavailable("The warehouse could not complete this query. Try again or ask your workspace administrator.")
            manifest = response.manifest
            if manifest is None or manifest.truncated:
                raise DataUnavailable("The warehouse returned an incomplete result. Please retry.")
            data = []
            chunk = response.result
            while chunk is not None:
                data.extend(json.loads(row[0]) for row in chunk.data_array or [])
                if chunk.next_chunk_index is None:
                    break
                if time.monotonic() >= deadline:
                    raise DataUnavailable("The warehouse result took too long to load. Please retry.")
                chunk = self.sql.get_statement_result_chunk_n(response.statement_id, chunk.next_chunk_index)
            if len(data) != manifest.total_row_count:
                raise DataUnavailable("The warehouse returned an incomplete result. Please retry.")
            return data, time.monotonic() - started
        except DatabricksError as error:
            if getattr(error, "error_code", None) in {"PERMISSION_DENIED", "UNAUTHENTICATED"}:
                raise DataUnavailable("Databricks access was denied. Check sign-in and app resource permissions.") from None
            raise DataUnavailable("Databricks is unavailable. Retry in a moment.") from None
        except (ValueError, OSError):
            raise DataUnavailable("Could not reach or read from Databricks. Check connectivity and sign-in.") from None

    def addresses(self) -> WarehouseRead:
        return self.read(f"""
            SELECT to_json(struct({ADDRESS_LOOKUP})) FROM (
                SELECT DISTINCT {ADDRESS_LOOKUP} FROM {GOLD}
            )
        """)

    def service(self, service: str) -> WarehouseRead:
        return self.read(f"""
            WITH site_claims AS (
                SELECT facility_id, source_content_id, sort_array(collect_set(claim_text)) AS sibling_site_claims
                FROM {VERIFIED}
                WHERE service_key = :service AND claim_type = 'candidate_claim'
                  AND evidence_status = 'supported_claim' AND has_service_negation = false
                  AND instr(source_text, claim_text) > 0
                  AND lower(claim_text) RLIKE ' (at|in) '
                GROUP BY facility_id, source_content_id
            ), candidates AS (
                SELECT {GOLD_FIELDS}, s.sibling_site_claims
                FROM {GOLD} g LEFT JOIN site_claims s USING (facility_id, source_content_id)
                WHERE g.service_key = :service
            )
            SELECT to_json(struct(*)) FROM candidates
        """, service=service)

    def evidence(self, service: str, facility: str, offset: int) -> WarehouseRead:
        return self.read(f"""
            SELECT to_json(struct(*)) FROM (
                SELECT DISTINCT facility_id, source_content_id, source_field, claim_text,
                    source_text, source_urls_raw, source_attribution_status, evidence_status,
                    verification_reason
                FROM {VERIFIED}
                WHERE facility_id = :facility AND service_key = :service
                  AND claim_text IS NOT NULL AND instr(source_text, claim_text) > 0
            )
            ORDER BY CASE evidence_status WHEN 'conflicting' THEN 0 WHEN 'rejected' THEN 1
                         WHEN 'uncertain' THEN 2 ELSE 3 END,
                     source_field, source_content_id, claim_text, source_text, source_urls_raw,
                     source_attribution_status, verification_reason
            LIMIT {EVIDENCE_PAGE + 1} OFFSET :offset
        """, service=service, facility=facility, offset=offset)


def search(query, addresses, service_rows, *, audit=None):
    # Address-only rows never qualify as service candidates. They preserve
    # cross-service place disambiguation without loading irrelevant claim text.
    return recommend(query, [*addresses, *service_rows], audit=audit)


def source_links(raw):
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        parsed = raw
    urls = parsed if isinstance(parsed, list) else [parsed]
    safe = []
    for url in urls:
        if not isinstance(url, str) or any(char.isspace() or ord(char) < 32 for char in url):
            continue
        try:
            parts = urlsplit(url)
            if (parts.scheme in {"http", "https"} and parts.hostname
                    and parts.username is None and parts.password is None
                    and "\\" not in url and url not in safe):
                safe.append(url)
        except ValueError:
            continue
    return safe
