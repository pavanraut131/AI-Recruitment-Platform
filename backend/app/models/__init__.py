from app.models.resume import Resume, ResumeStatus

# TODO(E0-10): remove. Campaign and Candidate moved to app.modules.hiring.models (E0-09). The
# re-export also guarantees that importing ``Resume`` registers the hiring mappers its string
# relationships ("Campaign", "Candidate") resolve to, e.g. in a Celery worker that imports only
# ``app.models.resume``. When Resume moves into ingestion and the cross-module relationships go,
# this package goes too.
from app.modules.hiring.models import Campaign, CampaignStatus, Candidate

__all__ = ["Campaign", "CampaignStatus", "Resume", "ResumeStatus", "Candidate"]
