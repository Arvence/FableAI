from app.contracts import ErrorCode, ErrorInfo


ERROR_STATUSES = {"unauthorized": 401, "credential_not_found": 404, "credential_revoked": 409, "project_not_found": 404, "project_conflict": 409, "invalid_request": 422, "conversation_not_found": 404, "entry_not_found": 404, "reference_not_found": 404, "version_conflict": 409, "request_in_progress": 409, "submission_conflict": 409, "processing_interrupted": 409, "reference_import_failed": 422, "generation_failed": 502, "rate_limited": 429, "storage_failed": 503, "access_denied": 403, "internal_error": 500}


class RepositoryError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        self.error = ErrorInfo(code=code, message=message)
        self.status = ERROR_STATUSES[code]
        super().__init__(message)
