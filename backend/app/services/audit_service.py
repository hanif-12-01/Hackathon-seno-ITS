import json
from typing import Any, Optional
from sqlalchemy.orm import Session
from app.models.db_models import AuditLog


class AuditService:
    @staticmethod
    def record_log(
        db: Session,
        action_type: str,
        actor_id: str,
        details: Any,
        case_id: Optional[str] = None,
    ) -> AuditLog:
        """Mencatat aktivitas mutasi atau keputusan ke audit log."""
        details_str = json.dumps(details) if isinstance(details, (dict, list)) else str(details)
        log_entry = AuditLog(
            case_id=case_id,
            action_type=action_type,
            actor_id=actor_id,
            details=details_str,
        )
        db.add(log_entry)
        db.flush()
        return log_entry
