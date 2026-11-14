"""내부 데이터 모델."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ParsedLaunch:
    """LL2에서 파싱된 발사 1건. parser → repository 사이 전달용."""
    name: str
    provider: str
    country: Optional[str] = None
    rocket: Optional[str] = None
    launch_time: Optional[datetime] = None
    launch_site: Optional[str] = None
    status: str = "Unknown"
    source_url: Optional[str] = None
    raw: dict = field(default_factory=dict)

